# Sample footage

Stock clips under the Pexels License (free to use, no attribution required). Violation labels shown
on them in the dashboard are staged for the demo; they do not describe the people filmed.

| File | Source |
|---|---|
| unsafe_lift.mp4 | "Person Carrying Box", Tima Miroshnichenko, https://www.pexels.com/video/person-carrying-box-6169604/ |
| restricted_zone.mp4 | "Warehouse Operations with Forklift and Workers", Usman AbdulrasheedGambo, https://www.pexels.com/video/warehouse-operations-with-forklift-and-workers-32243654/ |
| no_gloves.mp4 | "People Working Together in a Factory", Amar Preciado, https://www.pexels.com/video/people-working-together-in-a-factory-8633310/ |
| no_hairnet.mp4 | "Strawberry Packing Process in Modern Factory", Kiwi and Camera, https://www.pexels.com/video/strawberry-packing-process-in-modern-factory-39206268/ |

These files are not in the repo. To get them locally, open each link, choose Free download > HD 1280x720,
save under the file name above, then make the posters:
`for f in *.mp4; do ffmpeg -y -ss 2 -i $f -frames:v 1 -vf scale=960:-1 ${f%.mp4}.jpg; d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $f); ffmpeg -y -ss $(python3 -c "print(max(0.5,$d-1.5))") -i $f -frames:v 1 -vf scale=960:-1 ${f%.mp4}_after.jpg; done`
Without them, mock mode still runs; camera tiles fall back to the clip-less view.
