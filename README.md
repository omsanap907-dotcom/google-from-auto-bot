# PDF Paraphrasing Processor

A Railway/Flask application that:

1. accepts a PDF,
2. extracts text with pypdf,
3. splits it into chunks below ExampdfX's documented 3,000-character input limit,
4. submits each chunk to the ExampdfX paraphrasing tool in Formal mode,
5. combines the rewritten chunks into a DOCX.

Target site: https://exampdfx.com/paraphrasing-tool

ExampdfX currently advertises free/no-login paraphrasing, five rewrite modes, and up to 3,000 characters per request. This project uses a 2,800-character safety limit per request.

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
python formpilot.py
```

The application processes the target website with Playwright. Because it depends on a third-party webpage, selectors or behavior can change. No CAPTCHA-solving or CAPTCHA bypass is implemented.
