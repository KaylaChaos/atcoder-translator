# AtCoder Translator FunctionGraph Worker

This package is a dependency-free Huawei FunctionGraph worker for:

1. Fetching AtCoder contest tasks with a registered account session.
2. Extracting the English task statement.
3. Translating translatable HTML blocks with an OpenAI-compatible API.
4. Saving raw and translated files to Alibaba OSS.
5. Sending the translated HTML file to a WeCom group robot.

The current ZIP worker sends HTML files, not PDF files. High-quality PDF output should be added with a container function that includes Chromium.

## FunctionGraph

- Runtime: Python 3.9
- Handler: `index.handler`
- Timeout: 120s or higher for translation
- Memory: 512MB recommended
- Upload ZIP: `atcoder_worker_function.zip`

Use `WORKER_MODE=probe` for connectivity testing. Use `WORKER_MODE=worker` for the real workflow.

## Required Environment Variables

```text
WORKER_MODE=worker

ATCODER_CONTEST_ID=auto
ATCODER_AUTO_CONTEST_MODE=active_or_next
ATCODER_REVEL_SESSION=<REVEL_SESSION value only>

ALI_OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
ALI_OSS_BUCKET=<bucket>
ALI_ACCESS_KEY_ID=<access-key-id>
ALI_ACCESS_KEY_SECRET=<access-key-secret>
ALI_OSS_PREFIX=atcoder-translator

OPENAI_BASE_URL=https://sub2api.11xy.cn
OPENAI_API_KEY=<key>
OPENAI_MODEL=<model>
OPENAI_API_MODE=auto

WECOM_WEBHOOK_KEY=<wecom-robot-key>
WECOM_SEND=1
```

## Optional Environment Variables

```text
ATCODER_TASK_IDS=abc465_a,abc465_b
MAX_TASKS_PER_RUN=8
FORCE_REPROCESS=0
TRANSLATE_BATCH_SIZE=18
OPENAI_TRANSLATE_MAX_TOKENS=4096
WECOM_PROBE_SEND=0
```

## OSS Layout

```text
atcoder-translator/
  status/<contest_id>.json
  raw/<contest_id>/<task_id>.html
  translated/<contest_id>/<task_id>.zh.html
  translated/<contest_id>/<task_id>.meta.json
  probe/*.json
```

## Probe Mode

Set:

```text
WORKER_MODE=probe
```

The function checks:

- AtCoder login state.
- OSS put/get/delete.
- OpenAI API.
- WeCom webhook configuration.

Set `WECOM_PROBE_SEND=1` only when you want to send a real test message.

## Worker Mode

Set:

```text
WORKER_MODE=worker
WECOM_SEND=1
```

For a scheduled ABC run, configure the timer to invoke this function around the contest window. The worker is idempotent: already sent tasks are skipped unless `FORCE_REPROCESS=1`.

## Automatic ABC Contest Detection

Set:

```text
ATCODER_CONTEST_ID=auto
ATCODER_AUTO_CONTEST_MODE=active_or_next
```

The worker resolves the contest id from AtCoder's contests page:

1. First ABC in `Active Contests`.
2. First ABC in `Upcoming Contests`.
3. First ABC in `Recent Contests`.
4. Largest ABC number as fallback.

An explicit event value still overrides auto detection:

```json
{"contest_id":"abc464"}
```

If you really want the largest listed ABC number, use:

```text
ATCODER_AUTO_CONTEST_MODE=latest_number
```

## Local HTML to PDF Test

Use `html_to_pdf.py` to render a translated HTML file to PDF with `watermark.png`.

Install once:

```powershell
pip install -r requirements-pdf.txt
python -m playwright install chromium
```

Render:

```powershell
python .\html_to_pdf.py .\sample_pdf_test.html -o .\sample_pdf_test.pdf --watermark .\watermark.png
```

Default watermark settings:

```text
mode: center
width: 180mm
margin: 14mm
opacity: 0.075
```

For a smaller corner watermark:

```powershell
python .\html_to_pdf.py .\sample_pdf_test.html -o .\sample_pdf_test.corner.pdf --watermark .\watermark.png --watermark-mode corner --watermark-width 42mm --watermark-opacity 0.16
```

## Container Function For PDF Output

The PDF worker needs Chromium, so deploy it as a FunctionGraph container image.

Files:

```text
Dockerfile
container_server.py
index.py
watermark.png
container-env.example.txt
```

Build locally:

```powershell
docker build -t atcoder-translator:pdf .
```

Run locally:

```powershell
docker run --rm -p 8000:8000 --env-file .\container-env.local.txt atcoder-translator:pdf
```

Probe:

```powershell
curl -X POST http://localhost:8000/invoke -H "Content-Type: application/json" -d "{\"mode\":\"probe\"}"
```

Worker test without sending:

```powershell
curl -X POST http://localhost:8000/invoke -H "Content-Type: application/json" -d "{\"contest_id\":\"abc465\"}"
```

Container env for PDF mode:

```text
WORKER_MODE=worker
OUTPUT_FORMAT=pdf
WECOM_SEND=1
WATERMARK_PATH=watermark.png
WATERMARK_WIDTH=180mm
WATERMARK_OPACITY=0.075
```

Push the image to Huawei SWR, then create a FunctionGraph function from that SWR image. The container listens on port `8000` and exposes `/invoke`.
