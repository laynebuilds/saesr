const nav=document.querySelector('nav');function navState(){nav.classList.toggle('float',scrollY>24)}addEventListener('scroll',navState,{passive:true});navState();

// hero: rotating zone + matching incident card
const ROT=[
 {z:'Loading dock.',cam:'Cam 01, loading dock',t:'2:14 AM',type:'Unsafe lift',d:'Back bent 62 degrees with straight legs while lifting.',fix:'Worker coached'},
 {z:'Forklift lane.',cam:'Cam 02, forklift lane',t:'6:41 AM',type:'Restricted zone',d:'Person on foot in the forklift lane.',fix:'Forklift paused'},
 {z:'Packing line.',cam:'Cam 03, packing',t:'11:08 AM',type:'No gloves',d:'Bare hands on product at the packing table.',fix:'Batch held'},
 {z:'Cold store.',cam:'Cam 04, cold store',t:'3:52 PM',type:'No hairnet',d:'Hair uncovered over open product.',fix:'Worker coached'},
 {z:'At 3 AM.',cam:'Cam 01, loading dock',t:'3:00 AM',type:'No hard hat',d:'No hard hat under the overhead rack.',fix:'Shift lead notified'}
];
const rot=document.getElementById('rot'),card=document.getElementById('card'),steps=[...document.querySelectorAll('#steps li')];
const $=id=>document.getElementById(id);let ri=0,timers=[];
function play(){steps.forEach(s=>s.classList.remove('on'));timers.forEach(clearTimeout);timers=steps.map((s,i)=>setTimeout(()=>s.classList.add('on'),500+i*650))}
function show(i){const r=ROT[i];rot.classList.add('out');card.classList.add('dim');
 setTimeout(()=>{rot.textContent=r.z;$('cCam').textContent=r.cam;$('cTime').textContent=r.t;$('cType').textContent=r.type;$('cDet').textContent=r.d;$('cFix').textContent=r.fix;rot.classList.remove('out');card.classList.remove('dim');play()},320)}
play();if(!matchMedia('(prefers-reduced-motion: reduce)').matches)setInterval(()=>{ri=(ri+1)%ROT.length;show(ri)},4200);

// pinned walkthrough
const stage=document.getElementById('stage');
if(stage){const beats=[...stage.querySelectorAll('.beat')],imgs=[...stage.querySelectorAll('.shots img')],dots=[...stage.querySelectorAll('.dots i')];let cur=-1;
 function set(n){if(n===cur)return;cur=n;[beats,imgs,dots].forEach(g=>g.forEach((el,j)=>el.classList.toggle('on',j===n)))}
 function upd(){const r=stage.getBoundingClientRect(),total=stage.offsetHeight-innerHeight;const t=Math.min(.999,Math.max(0,-r.top/total));set(Math.floor(t*beats.length))}
 addEventListener('scroll',upd,{passive:true});addEventListener('resize',upd);upd()}

// problem path draws as you scroll
const route=document.getElementById('route');
if(route){const path=document.getElementById('p'),L=path.getTotalLength();path.style.strokeDasharray=L;path.style.strokeDashoffset=L;
 function updp(){const r=route.getBoundingClientRect();const t=Math.min(1,Math.max(0,(innerHeight*.72-r.top)/r.height));path.style.strokeDashoffset=L*(1-t)}
 addEventListener('scroll',updp,{passive:true});addEventListener('resize',updp);updp()}

const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)e.target.classList.add('in')}),{threshold:.25});
document.querySelectorAll('.stop,.rv,.giant').forEach(el=>io.observe(el));
document.querySelectorAll('.giant span').forEach((s,i)=>s.style.transitionDelay=(i*.06)+'s');
