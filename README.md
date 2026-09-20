# PDF Paraphrasing Processor

Self-contained PDF-to-DOCX paraphrasing service using Flask and an open-source T5 paraphrasing model.

The final version does not automate ExampdfX or another third-party webpage. It performs English paraphrasing locally with Vamsi/T5_Paraphrase_Paws and Hugging Face Transformers.

Workflow:
1. Upload PDF.
2. Extract text page by page.
3. Split into model-sized chunks.
4. Paraphrase in batches.
5. Rebuild the pages into a DOCX.
6. Download the DOCX.

The Hugging Face model card identifies Vamsi/T5_Paraphrase_Paws as an English paraphrase-generation T5 model trained on PAWS and documents direct Transformers usage.

Important: this tool cannot guarantee a particular plagiarism-checker score. Keep required citations and review the rewritten document.

Environment variables:
MODEL_ID
BATCH_SIZE
MAX_UNIT_WORDS
MAX_INPUT_TOKENS
MAX_OUTPUT_TOKENS
TORCH_THREADS

Default model:
Vamsi/T5_Paraphrase_Paws
