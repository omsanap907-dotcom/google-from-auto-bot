from playwright.sync_api import sync_playwright

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdxFUZJvRxZY-iM7BRtGntrZV6CkVqId0g3ALN5Krzz9LEv3w/viewform?usp=dialog"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    page.goto(FORM_URL)
    page.wait_for_load_state("domcontentloaded")

    questions = page.locator('[role="listitem"]')

    print("\n========== QUESTIONS & OPTIONS ==========\n")

    for i in range(questions.count()):
        q = questions.nth(i)
        text = q.inner_text().strip()

        if not text:
            continue

        print(f"\n--- QUESTION {i + 1} ---")
        print(text)

        radios = q.locator('[role="radio"]')
        checkboxes = q.locator('[role="checkbox"]')

        if radios.count():
            print("\nOPTIONS:")
            for j in range(radios.count()):
                print(" •", radios.nth(j).get_attribute("aria-label"))

        elif checkboxes.count():
            print("\nOPTIONS:")
            for j in range(checkboxes.count()):
                print(" •", checkboxes.nth(j).get_attribute("aria-label"))

    input("\nPress Enter to close...")
    browser.close()