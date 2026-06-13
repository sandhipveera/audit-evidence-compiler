#!/usr/bin/env bash
# Insert a "Splunk MLTK" beat into the web demo video (aec-demo-final.mp4):
# at the Clip-A boundary, splice a still of the updated architecture diagram
# (which shows the Splunk AI · MLTK node) under a voiceover line in the
# presenter's voice. Single re-encode pass, so there are no concat seams and the
# existing B–F voiceover stays perfectly in sync (it just shifts later as a block).
#
#   bash scripts/insert-mltk-web.sh [IN.mp4] [OUT.mp4] [VO.mp3] [INSERT_SEC] [ARCH.png]
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IN="${1:-/home/veera/aec-demo-final.mp4}"
OUT="${2:-/home/veera/aec-demo-final-mltk.mp4}"
VO="${3:-/tmp/mltk-vo.mp3}"
INSERT="${4:-27.0}"                              # Clip-A boundary in the final
ARCH="${5:-$REPO_DIR/web/static/architecture-pipeline.png}"
BG="0x0c0b12"                                    # deck background

dur(){ ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$1"; }
VOD="$(dur "$VO")"
SEG="$(awk -v a="$VOD" 'BEGIN{print a+0.4}')"    # VO + breathing room

ffmpeg -y -i "$IN" -loop 1 -t "$SEG" -i "$ARCH" -i "$VO" -filter_complex "
[0:v]fps=30,scale=1920:916:force_original_aspect_ratio=decrease,pad=1920:916:-1:-1:color=${BG},setsar=1,split=2[vA][vB];
[vA]trim=0:${INSERT},setpts=PTS-STARTPTS[v0];
[vB]trim=start=${INSERT},setpts=PTS-STARTPTS[v2];
[1:v]scale=1920:916:force_original_aspect_ratio=decrease,pad=1920:916:-1:-1:color=${BG},setsar=1,fps=30,trim=0:${SEG},setpts=PTS-STARTPTS[v1];
[0:a]aresample=48000,asplit=2[aA][aB];
[aA]atrim=0:${INSERT},asetpts=PTS-STARTPTS[a0];
[aB]atrim=start=${INSERT},asetpts=PTS-STARTPTS[a2];
[2:a]aresample=48000,apad,atrim=0:${SEG},asetpts=PTS-STARTPTS[a1];
[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
  -c:a aac -ar 48000 -ac 2 "$OUT" -loglevel error

echo "✓ wrote $OUT"
printf "  inserted %.1fs MLTK segment at %ss; total now " "$SEG" "$INSERT"; dur "$OUT" | awk '{printf "%d:%02d\n",$1/60,$1%60}'
