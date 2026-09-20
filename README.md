# PDF Paraphrasing Processor

Browser-first PDF-to-DOCX paraphrasing app.

## Final architecture

The GitHub Pages interface runs the rewrite model in the browser. It does not need a Railway API or a third-party paraphrasing website.

The app uses:
- PDF.js for PDF text extraction in the browser.
- Hugging Face Transformers.js for text-to-text generation.
- Xenova/flan-t5-small, a verified Transformers.js-compatible ONNX model.
- docx.js for DOCX generation in the browser.
- WebGPU when available, otherwise WASM.

Model:
https://huggingface.co/Xenova/flan-t5-small

## Workflow

1. Open the GitHub Pages site.
2. Choose a PDF.
3. Text is extracted locally in the browser.
4. The text is split into small chunks.
5. Each chunk is paraphrased locally in the browser.
6. Live progress is shown.
7. A DOCX is generated and downloaded.

The first run downloads the model. The browser may cache the model for later runs.

## Privacy

The uploaded PDF text is processed in the browser by the GitHub Pages app. This interface does not send the PDF to the Railway backend.

The browser still needs internet access to download JavaScript libraries and model files.

## Important

This is a paraphrasing tool and cannot guarantee a particular plagiarism-checker score. Review the generated document and retain citations required for your work.

## GitHub Pages

https://omsanap907-dotcom.github.io/google-from-auto-bot/
