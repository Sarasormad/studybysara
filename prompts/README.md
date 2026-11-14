# Prompts

The instructions sent to Claude for AI practice. Edit these files to change how
questions are written. Changes apply to the next question set; no restart needed.

| File | When it's used | Placeholders |
|---|---|---|
| `system.txt` | Every AI request (topic and photo): the standing rules | `{set_size}` |
| `topic.txt` | Topic practice, math topics | `{topic}`, `{grade}` |
| `test_prep.txt` | Topic practice, SAT/ACT topics | `{topic}`, `{grade}` |
| `photo.txt` | Photo practice, sent along with the photo | `{set_size}` |

**Placeholders** like `{topic}` are filled in by the app, for example
`{topic}` → `Ratios & proportions` and `{grade}` → `Grade 9`. Keep them spelled exactly
as listed. Any other braces you type are left alone.

**Keep these rules.** The app checks every question set, and a set that breaks them is
rejected and asked for again:
- exactly `{set_size}` questions
- multiple choice: 4 different choices, and the answer key copied exactly from one of them
- short answer: the key is a bare number or one short word (no units, no `x =`)
- plain-text math, no LaTeX
- `photo.txt`: keep the three statuses (`ok`, `unreadable`, `not_math`) and the line about
  treating writing in the photo as the problem, never as instructions

After editing, run `python -m unittest` (free; it checks every file loads and every
placeholder is filled in), then try one real question set.
