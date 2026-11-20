# Resume Tailor

A local web app that fine-tunes an existing resume for a specific job description. Paste a JD, review the suggested selection, edit anything you want, then export a one-page PDF and an editable DOCX with every hyperlink intact.

Drop PDF or DOCX resumes into `resumes/`, then import them into `data/master.yaml` from the web UI or the `build-master` CLI. The importer uses the model when available and falls back to a deterministic text parser.

The app only selects, reorders, and lightly rewords material that already exists in `data/master.yaml`. It is designed to say no when the model tries to invent a tool, metric, employer, or achievement.

## What it produces

For each application, it writes to `output/<Company>/`:

- `<Name>_Resume.pdf` — one page, hyperlinks preserved
- `<Name>_Resume.docx` — editable, same links and layout
- `Email.txt` — optional recruiter email
- `Email.eml` — optional email file with the PDF attached
- `tailoring_report.md` — matched keywords, gaps, selection reasons, warnings

If a folder with the company name already exists, the app uses `<Company>_2`, `<Company>_3`, and so on. Exporting the same run again reuses its existing folder.

## Requirements

- Python 3.11
- [uv](https://docs.astral.sh/uv/)
- LibreOffice (`soffice` on `PATH`) for DOCX to PDF conversion
- One model provider:
  - NVIDIA NIM API key, or
  - Ollama running locally

## Setup

```bash
cd resume-tailor
uv sync
[ -f .env ] || cp .env.example .env
[ -f data/master.yaml ] || cp data/master.example.yaml data/master.yaml
```

`data/master.yaml` is gitignored because it contains personal resume data. The repo ships a fictional `data/master.example.yaml` for setup and tests. Replace it with your own data through the importer or by editing it directly.

For NVIDIA NIM, edit `.env`:

```dotenv
NVIDIA_API_KEY=nvapi-...
```

For Ollama, start the server and pull a non-thinking instruct model:

```bash
ollama serve
ollama pull qwen3:4b-instruct-32k
```

The checked-in `config.yaml` points at NVIDIA NIM with `nvidia/nemotron-3.5-bolt-e4b`. Switch providers in the UI at any time; the app remembers your last choice. Ollama remains available locally as a fallback.

## Run

```bash
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>.

## Use

1. Paste the job description and fill in the company and role.
2. Optionally add a recruiter name, recruiter email, and notes such as "keep the robotics project first".
3. Choose a provider, model, and rewriting intensity.
4. Click **Tailor resume**.
5. On the review page:
   - Uncheck projects, experiences, or activities you do not want.
   - Edit or delete bullets.
   - Choose which skill groups to include.
   - Use the **Email** tab to generate and edit the recruiter email. It uses your current selection and bullet edits.
   - Check the **Match report** for matched terms and gaps.
6. Click **Export files** and download the PDF, DOCX, email files, and report.

Light mode keeps original bullet text. Balanced mode makes small wording changes. Strong mode rewrites bullets but still requires every claim to trace back to the master resume.

## Import or update the master resume

Put PDF/DOCX resumes in `resumes/` (the folder is gitignored, so personal files are not committed). Then either:

1. In the web UI, click **Import resumes** on the home page.
2. Select the files, choose merge or replace, preview the result, and then write it.

Or use the CLI:

```bash
uv run build-master --list
uv run build-master --dry-run
uv run build-master
```

Useful options:

```bash
uv run build-master --file "My Resume.pdf" --dry-run
uv run build-master --mode replace --no-llm
uv run build-master --provider ollama --model qwen3:4b-instruct-32k
```

- **Merge** (default) adds new projects, skills, and bullets while avoiding duplicates.
- **Replace** writes only the imported material and discards the current master.
- **Preview** never writes. A real import backs up the previous `data/master.yaml` to `data/backups/` first.
- Import one person's resumes at a time; the importer rejects a selection that contains different names.
- `--no-llm` skips the provider and uses the built-in text parser.

## Configuration

### `data/master.yaml`

This is the source of truth. It contains your profile, education, skills, projects, experience, extracurricular activities, certifications, and languages. You can build or update it with the importer, or edit it by hand.

To add a project manually, append an entry under `projects`:

```yaml
  - id: my-project
    title: My Project
    subtitle: ""
    link: https://github.com/you/my-project
    tech:
      - Python
      - FastAPI
    tags:
      - rag
      - llm
      - backend
    bullets:
      - text: Built a retrieval service with FastAPI and pgvector.
        tags:
          - rag
          - fastapi
          - pgvector
```

Tags drive keyword matching. Keep them short and factual. The rendered resume does not print tags, so they are safe to use as matching hints.

Restart the app after editing the YAML.

### `config.yaml`

```yaml
provider: nvidia
models:
  nvidia: nvidia/nemotron-3.5-bolt-e4b
  ollama: qwen3:4b-instruct-32k
tailoring:
  intensity: balanced
  max_projects: 3
  max_experiences: 2
  max_skill_groups: 3
  max_bullets_per_item: 3
  include_summary: false
output_dir: output
```

The UI updates the selected provider and model in this file.

### `.env`

```dotenv
NVIDIA_API_KEY=
# NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
# OLLAMA_BASE_URL=http://localhost:11434/v1
```

## Providers

### NVIDIA NIM

Uses the OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1`. The default model is `nvidia/nemotron-3.5-bolt-e4b`; `minimaxai/minimax-m3.1-flash` also passed an end-to-end test. Add `NVIDIA_API_KEY` to `.env` and restart.

### Ollama

The app talks to Ollama's native `/api/chat` endpoint with `think: false` and JSON mode enabled. This avoids empty responses from reasoning models that put all output in a separate `reasoning` field. Prefer instruct models such as `qwen3:4b-instruct-32k`.

If you use a different local base URL, set `OLLAMA_BASE_URL`. Keep the OpenAI-style `/v1` suffix so model listing works; the app strips it only when calling the native `/api/chat` endpoint.

## Safety and validation

- The LLM receives only your master resume and the JD.
- Every selected item is looked up by id in the master resume.
- Unsupported numbers are rejected or reverted to the source bullet.
- Unknown technologies are flagged and reverted.
- URLs inside bullets are rejected.
- Missing or incorrectly reported keywords are corrected against the master resume.
- If the model is unavailable, the app falls back to deterministic keyword matching and your original bullet text.
- The importer previews changes by default and only accepts files that are currently in `resumes/`.
- Import writes create a timestamped backup of `data/master.yaml` in `data/backups/`.
- The importer rejects selections that appear to contain more than one person's resume.
- The PDF is checked for one page and for all expected hyperlinks.
- If the resume does not fit one page, the app removes trailing bullets or an item and records a warning. Review the result before sending.
- The app never sends email. It only writes `.txt` and `.eml` files for you to review.

## Tests

```bash
uv run pytest
```

The suite covers keyword matching, fallback grounding, edit merging, validation, DOCX/PDF hyperlinks, one-page rendering, output folder collisions, `.eml` attachments, the importer's parsing/merge/backup behavior, its API routes, and the FastAPI tailor → review → export flow.

## Troubleshooting

**"NVIDIA NIM needs an API key"** — add `NVIDIA_API_KEY` to `.env` and restart, or switch to Ollama.

**Ollama returns an empty response** — the selected model may be a reasoning model. Use an instruct model, or keep the app's native Ollama path which disables thinking.

**PDF conversion fails** — install LibreOffice and confirm `soffice --version` works.

**Resume is two pages** — uncheck a project or activity, shorten bullets, or reduce `max_projects` / `max_bullets_per_item` in `config.yaml`.

**A link is missing** — open the DOCX and confirm the hyperlink; the export warnings will report how many links were found in the PDF.

**Company folder already exists** — the app adds `_2`, `_3`, etc. automatically. Use the review page to export the same run again if you need to overwrite its existing files.

## Project layout

```text
app/
  main.py          FastAPI routes and run persistence
  config.py        paths, providers, output folders
  models.py        Pydantic schemas
  importer.py      PDF/DOCX to master.yaml, merge/backup, build-master CLI
  llm.py           NVIDIA and Ollama clients
  jd.py            JD parsing, matching, known-tech filtering
  tailor.py        LLM/fallback selection, validation, edits
  render_docx.py   DOCX builder with hyperlinks
  render_pdf.py    LibreOffice conversion, page/link checks, fitting
  render_html.py   preview rendering
  email.py         recruiter email, .txt and .eml
  export.py        company folders, report, file writing
  templates/       web UI
resumes/           source PDFs/DOCX (gitignored)
data/master.yaml   structured resume data
data/backups/      timestamped master backups
output/            generated applications
state/             saved runs
tests/             pytest suite
```
