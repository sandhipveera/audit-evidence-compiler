#!/usr/bin/env bash
# Record a short, self-contained "Splunk MLTK runs at runtime" CLI clip to splice
# into the backend demo video. No LLM keys needed — uses --sample + --no-llm and
# the recorded MLTK fixture in the sample snapshot. This is an *additive* segment
# (the [1/6] load + [2/6] Splunk MLTK beat), so the rest of the existing
# voiceover stays in sync; you only narrate the new MLTK moment.
#
# Produces:  <OUT>.cast  <OUT>.gif  <OUT>.mp4   (default OUT=$HOME/mltk-clip)
# Requires:  asciinema, agg, ffmpeg
#
#   bash scripts/record-mltk-clip.sh [OUT_BASENAME]
set -euo pipefail

export AEC_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HOME/mltk-clip}"
INNER="$(mktemp)"
trap 'rm -f "$INNER"' EXIT

cat > "$INNER" <<'INNERSH'
#!/usr/bin/env bash
cd "$AEC_REPO"
# shellcheck disable=SC1091
source "$AEC_REPO/.venv/bin/activate"
set -a; [ -f "$AEC_REPO/.env" ] && source "$AEC_REPO/.env"; set +a
export FORCE_COLOR=1          # Rich keeps ANSI colour even though stdout is piped
clear; sleep 0.8
printf '\033[1;37mAudit Evidence Auto-Compiler — Splunk MLTK runs at runtime\033[0m\n'
printf '\033[0;90mSplunk Agentic Ops Hackathon 2026 — Security Track\033[0m\n\n'; sleep 1.6
printf '\033[1;32m$\033[0m '
cmd='aec_demo --sample soc2-cc61'
for ((i=0; i<${#cmd}; i++)); do printf '%s' "${cmd:$i:1}"; sleep 0.045; done
sleep 0.5; echo
# Stop after the MLTK beat so the clip stays focused (skip the panel + snapshot dump).
aec_demo --sample soc2-cc61 --no-llm 2>&1 | sed -n '1,/scored the evidence/p'
echo; sleep 2.8
INNERSH
chmod +x "$INNER"

asciinema rec "$OUT.cast" --overwrite --command "bash $INNER"
agg --theme asciinema --font-size 16 "$OUT.cast" "$OUT.gif"
ffmpeg -y -i "$OUT.gif" \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,fps=30" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p "$OUT.mp4" -loglevel error

echo "✓ wrote $OUT.cast / $OUT.gif / $OUT.mp4"
