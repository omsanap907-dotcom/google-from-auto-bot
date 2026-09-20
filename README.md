# PDF Paraphrasing Processor

Browser-first PDF paraphrasing app with DOCX export.

## Final architecture

The main GitHub Pages interface runs the rewrite model **in the browser**. It does not need a Railway API or third-party paraphrasing website.

The app uses:
- PDF.js to extract PDF text in the browser.
- Hugging Face Transformers.js to run an ONNX/T5 paraphrasing model in the browser.
- docx.js to create the DOCX in the browser.
- WebGPU when available, otherwise WASM.

Model:
- \`sk1729271/revision-assistant-rewrite-plag\`
- Its model card provides an int8 quantized ONNX model for browser/Transformers.js use and a scientific-text paraphrasing prefix.

## Workflow

1. Open the GitHub Pages site.
2. Choose a PDF.
3. The browser reads the PDF locally.
4. Text is split into small chunks.
5. The local browser model rewrites each chunk.
6. Progress is shown live.
7. A DOCX is generated and downloaded.

The first run downloads the browser model from Hugging Face. Later runs can use the browser cache.

## Privacy

The application code does not send the uploaded PDF to a Railway backend or paraphrasing website. PDF extraction and model inference happen in the browser. The browser still needs internet access to download the JavaScript libraries and model files.

## Important

This is a paraphrasing tool, not a guarantee of a particular plagiarism-checker score. Review the output and keep citations required by your assignment.

## Current GitHub Pages URL

https://omsanap907-dotcom.github.io/google-from-auto-bot/
