#!/usr/bin/env bash
# Adds a small local language model (via Ollama) for the companion's chat. Run on the Pi.
# Usage: bash setup-local-llm.sh [model]    default: qwen2.5:1.5b (about 1 GB, suits a 4 GB Pi 4)
set -e
MODEL="${1:-qwen2.5:1.5b}"
sudo apt-get install -y curl zstd python3-pil
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
# Keep the model from disturbing Klipper: lowest priority, at most 2 cores, unload soon after use
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/limits.conf >/dev/null <<'CONF'
[Service]
Nice=19
CPUQuota=200%
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_KEEP_ALIVE=2m
CONF
sudo systemctl daemon-reload
sudo systemctl restart ollama
ollama pull "$MODEL"
sudo mkdir -p /etc/systemd/system/klipper-companion.service.d
sudo tee /etc/systemd/system/klipper-companion.service.d/llm.conf >/dev/null <<CONF
[Service]
Environment=LLM_URL=http://127.0.0.1:11434/v1/chat/completions
Environment=LLM_MODEL=$MODEL
CONF
sudo systemctl daemon-reload
sudo systemctl restart klipper-companion
echo "Done. Chat is paused during prints to protect print timing."
