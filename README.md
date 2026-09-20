# PDF Paraphrasing Processor

Final self-contained PDF-to-DOCX paraphrasing service.

The project no longer automates ExampdfX or another third-party webpage. It runs the open-source Vamsi/T5_Paraphrase_Paws model in GGUF Q4_K_M format through llama-cpp-python.

The GGUF model repository lists Q4_K_M at about 137 MB and describes it as a balanced quantization of Vamsi/T5_Paraphrase_Paws.

Workflow:
1. Upload PDF.
2. Extract text page by page.
3. Split into small English text chunks.
4. Paraphrase locally on Railway.
5. Rebuild the pages into a DOCX.
6. Download the document.

Important:
- This is paraphrasing, not a guarantee of any plagiarism-checker score.
- Review the output and keep citations required by your assignment.
- The first run downloads the model and is slower.

Environment variables:
MODEL_REPO
MODEL_FILE
MODEL_CTX
MODEL_THREADS
MAX_UNIT_WORDS
MAX_OUTPUT_TOKENS

Default model:
tensorblock/T5_Paraphrase_Paws-GGUF
T5_Paraphrase_Paws-Q4_K_M.gguf
