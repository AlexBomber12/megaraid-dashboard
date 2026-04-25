This project's contributor and architectural rules live in AGENTS.md. Read AGENTS.md first. Claude-specific notes follow.

# Notes

- Prefer editing existing modules over creating parallel ones.
- When uncertain about a `storcli` flag or hardware behavior, write a small integration test under `tests/integration/` marked with skip rather than guessing.
- User prefers long-term sustainable solutions over short-term fixes; when given two options of similar effort, pick the one that survives kernel and OS upgrades.
