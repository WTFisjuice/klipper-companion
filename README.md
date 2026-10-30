# Klipper Companion
Read-only helper for Klipper: explains errors, checks printer.cfg, looks at your webcam. It never sends G-code or edits files.

- Install: `bash install.sh`, then open `http://<pi-ip>:8099`
- Updates: add the `[update_manager]` block the installer prints. Push to git, tap Update in Mainsail/Fluidd.
- Error rules: edit `rules.json` (pattern, title, fix). Bump `VERSION` on release.
- Local chat: `bash setup-local-llm.sh` installs Ollama with a ~1.5B model, throttled so Klipper keeps priority. Replies stream in slowly (a few tokens/sec). Chat pauses during prints (`LLM_WHILE_PRINTING=1` overrides). Or point `LLM_URL`/`LLM_MODEL` at any OpenAI-compatible server.
- Camera without a vision model: basic brightness and scene-change checks (needs `python3-pil`). With a vision model on another machine, set `VISION_URL` and `VISION_MODEL`. `WATCH_MINUTES=5` checks every 5 minutes while printing.
  Snapshots are sent to whatever server you configure, so use one you trust.
