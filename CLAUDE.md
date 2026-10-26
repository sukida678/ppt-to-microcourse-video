# CLAUDE.md

This project's operating manual lives in **[AGENTS.md](AGENTS.md)**. Read it first and
follow it exactly — it covers the preflight check, the four commands, how to write the
narration, which flags to change for which deck, how to verify the output, and the
failure table.

Quick start:

```bash
python scripts/doctor.py --fix
python scripts/make_course.py extract --deck <your-deck>.pptx --work build
#   ...write build/narration.json yourself — do not ship the template...
python scripts/make_course.py build --work build
```

The one rule people get wrong: **write the narration, don't paste the slide text.**
