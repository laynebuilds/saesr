"""Phone the floor manager with a short spoken briefing, over Photon's SIP voice calling.

Used when a worker does not follow the coaching: the same violation repeats after the fix (incident
status "recurring"). The call says, in a few words, who, what and what to do, then hangs up:

    "saesr safety alert. Worker number 1, Layne. Issue: unsafe lift again at the pack line after
     coaching, injury risk. Action: stop the task and retrain now. Goodbye."

How it works: macOS `say` renders the script to 8 kHz audio, encoded to G.711 mu-law here. A minimal
SIP user agent places the call to sip.spectrum.photon.codes over TLS (no registration; digest auth per
call with the project ID and secret), caller ID = the project's iMessage line, waits for the answer,
streams the audio as RTP, sends BYE. We only send audio, so NAT on the return path does not matter.

CLI test:  uv run python -m app.voice_call +14155550123 "Worker number 1, Layne. Issue: test. Action: none."
"""
import asyncio
import hashlib
import logging
import os
import random
import re
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import wave
from typing import Optional

log = logging.getLogger("saesr.voice")

SIP_HOST = os.environ.get("SPECTRUM_SIP_HOST", "sip.spectrum.photon.codes")
SIP_PORT = int(os.environ.get("SPECTRUM_SIP_PORT", "5061"))
RING_TIMEOUT_S = 45
VOICE = os.environ.get("CALL_VOICE", "Samantha")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


# ---- audio ---------------------------------------------------------------------------------------


def _ulaw(sample: int) -> int:
    """16-bit linear PCM -> G.711 mu-law byte."""
    BIAS, CLIP = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, CLIP) + BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not sample & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def speech_ulaw(text: str) -> bytes:
    """Render text with macOS `say` to 8 kHz mono and encode as mu-law."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "msg.wav")
        subprocess.run(["say", "-v", VOICE, "-r", "175", "-o", path, "--file-format=WAVE", "--data-format=LEI16@8000", text],
                       check=True, timeout=30)
        with wave.open(path) as w:
            frames = w.readframes(w.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return bytes(_ulaw(s) for s in samples)


# ---- SIP -----------------------------------------------------------------------------------------


def _rand(n: int = 10) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


def _digest(challenge: str, method: str, uri: str, user: str, password: str) -> str:
    params = dict(re.findall(r'(\w+)="?([^",]+)"?', challenge))
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', "algorithm=MD5"]
    if "auth" in params.get("qop", ""):
        cnonce, nc = _rand(16), "00000001"
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        parts += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    parts.append(f'response="{resp}"')
    if "opaque" in params:
        parts.append(f'opaque="{params["opaque"]}"')
    return "Digest " + ", ".join(parts)


class SipMessage:
    def __init__(self, raw: str):
        head, _, self.body = raw.partition("\r\n\r\n")
        lines = head.split("\r\n")
        self.first = lines[0]
        self.headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                self.headers.append((k.strip().lower(), v.strip()))
        m = re.match(r"SIP/2.0 (\d{3})", self.first)
        self.status = int(m.group(1)) if m else None

    def get(self, name: str) -> Optional[str]:
        compact = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length"}
        for k, v in self.headers:
            if compact.get(k, k) == name:
                return v
        return None

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k == name]


class SipCall:
    def __init__(self, line: str, to: str, user: str, password: str):
        self.line, self.to, self.user, self.password = line, to, user, password
        self.call_id = f"{_rand(20)}@saesr"
        self.from_tag = _rand(8)
        self.cseq = 0
        self.buf = b""

    def _connect(self) -> None:
        ctx = ssl.create_default_context()
        raw = socket.create_connection((SIP_HOST, SIP_PORT), timeout=10)
        self.sock = ctx.wrap_socket(raw, server_hostname=SIP_HOST)
        self.local_ip, self.local_port = self.sock.getsockname()[:2]

    def _send(self, text: str) -> None:
        self.sock.sendall(text.encode())

    def _recv(self, timeout: float) -> Optional[SipMessage]:
        self.sock.settimeout(timeout)
        while True:
            if b"\r\n\r\n" in self.buf:
                head, rest = self.buf.split(b"\r\n\r\n", 1)
                m = re.search(rb"(?im)^(?:content-length|l)\s*:\s*(\d+)", head)
                n = int(m.group(1)) if m else 0
                if len(rest) >= n:
                    self.buf = rest[n:]
                    return SipMessage((head + b"\r\n\r\n" + rest[:n]).decode(errors="replace"))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError("SIP connection closed")
            self.buf += chunk

    def _request(self, method: str, uri: str, cseq: int, branch: str, extra: list[str], body: str = "", to_tag: str = "") -> str:
        to = f"<sip:{self.to}@{SIP_HOST}>" + (f";tag={to_tag}" if to_tag else "")
        lines = [
            f"{method} {uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self.local_ip}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{self.line}@{SIP_HOST}>;tag={self.from_tag}",
            f"To: {to}",
            f"Call-ID: {self.call_id}",
            f"CSeq: {cseq} {method}",
            f"Contact: <sips:{self.line}@{self.local_ip}:{self.local_port};transport=tls>",
            "User-Agent: saesr/1.0",
            *extra,
        ]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        return "\r\n".join(lines) + "\r\n\r\n" + body

    def place(self, audio: bytes) -> str:
        """Dial, wait for answer, play audio, hang up. Returns an outcome word."""
        self._connect()
        rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rtp.bind(("0.0.0.0", 0))
        rtp_port = rtp.getsockname()[1]
        sdp = "\r\n".join([
            "v=0", f"o=saesr {int(time.time())} 1 IN IP4 {self.local_ip}", "s=saesr", f"c=IN IP4 {self.local_ip}", "t=0 0",
            f"m=audio {rtp_port} RTP/AVP 0 101", "a=rtpmap:0 PCMU/8000", "a=rtpmap:101 telephone-event/8000", "a=sendrecv", "a=ptime:20", "",
        ])
        uri = f"sip:{self.to}@{SIP_HOST}"
        auth: list[str] = []
        for attempt in range(2):
            self.cseq += 1
            branch = "z9hG4bK" + _rand(12)
            self._send(self._request("INVITE", uri, self.cseq, branch, auth, sdp))
            deadline = time.time() + RING_TIMEOUT_S
            while True:
                msg = self._recv(max(0.5, deadline - time.time()))
                if msg is None:
                    self._cancel(uri, branch)
                    return "no_answer"
                if msg.status is None:
                    continue  # a request from the server (e.g. OPTIONS); ignore
                log.info("SIP %s", msg.first)
                if msg.status < 200:
                    continue
                to_hdr = msg.get("to") or ""
                to_tag = (re.search(r"tag=([^;>\s]+)", to_hdr) or [None, ""])[1]
                if msg.status in (401, 407):
                    self._ack(uri, branch, msg, to_tag, final=False)
                    hdr = "WWW-Authenticate" if msg.status == 401 else "Proxy-Authenticate"
                    challenge = msg.get(hdr.lower()) or ""
                    auth = [f"{'Authorization' if msg.status == 401 else 'Proxy-Authorization'}: {_digest(challenge, 'INVITE', uri, self.user, self.password)}"]
                    break
                if msg.status >= 300:
                    self._ack(uri, branch, msg, to_tag, final=False)
                    return f"rejected_{msg.status}"
                return self._answered(msg, to_tag, rtp, audio)
        return "auth_failed"

    def _ack(self, uri: str, branch: str, msg: SipMessage, to_tag: str, final: bool) -> None:
        self._send(self._request("ACK", uri, self.cseq, branch, [], to_tag=to_tag))

    def _cancel(self, uri: str, branch: str) -> None:
        try:
            self._send(self._request("CANCEL", uri, self.cseq, branch, []))
        except OSError:
            pass

    def _answered(self, ok: SipMessage, to_tag: str, rtp: socket.socket, audio: bytes) -> str:
        contact = re.search(r"<([^>]+)>", ok.get("contact") or "")
        remote_uri = contact.group(1) if contact else f"sip:{self.to}@{SIP_HOST}"
        routes = []
        for rr in reversed(ok.all("record-route")):
            routes += [f"Route: {r.strip()}" for r in rr.split(",")]
        self._send(self._request("ACK", remote_uri, self.cseq, "z9hG4bK" + _rand(12), routes, to_tag=to_tag))
        c = re.search(r"c=IN IP4 (\S+)", ok.body)
        m = re.search(r"m=audio (\d+)", ok.body)
        if c and m:
            self._play(rtp, (c.group(1), int(m.group(1))), audio)
        else:
            log.warning("no SDP in answer, cannot play audio")
        self.cseq += 1
        self._send(self._request("BYE", remote_uri, self.cseq, "z9hG4bK" + _rand(12), routes, to_tag=to_tag))
        self._recv(5)
        self.sock.close()
        return "delivered"

    @staticmethod
    def _play(rtp: socket.socket, dest: tuple[str, int], audio: bytes) -> None:
        ssrc, seq, ts = random.getrandbits(32), random.getrandbits(16), random.getrandbits(32)
        lead = b"\xff" * 8000  # 1 s of silence so the first words are not clipped
        data = lead + audio + b"\xff" * 4000
        start = time.monotonic()
        for i in range(0, len(data), 160):
            chunk = data[i:i + 160].ljust(160, b"\xff")
            header = struct.pack("!BBHII", 0x80, (0x80 if i == 0 else 0) | 0, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            rtp.sendto(header + chunk, dest)
            seq += 1
            ts += 160
            wait = start + (i // 160 + 1) * 0.02 - time.monotonic()
            if wait > 0:
                time.sleep(wait)


def place_call(to: str, text: str, line: Optional[str] = None) -> str:
    """Blocking. Speaks `text` to `to` from the project's line. Returns the outcome."""
    user = _env("PHOTON_PROJECT_ID")
    password = _env("PHOTON_SIP_SECRET") or _env("PHOTON_API_KEY")
    line = line or _env("PHOTON_CALL_FROM")
    if not (user and password and line):
        raise RuntimeError("PHOTON_PROJECT_ID, PHOTON_API_KEY (or PHOTON_SIP_SECRET) and PHOTON_CALL_FROM are required")
    audio = speech_ulaw(text)
    return SipCall(line, to, user, password).place(audio)


async def call(to: str, text: str, line: Optional[str] = None) -> str:
    return await asyncio.to_thread(place_call, to, text, line)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    print(place_call(sys.argv[1], sys.argv[2]))
