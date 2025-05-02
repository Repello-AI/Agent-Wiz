import datetime
import os
import json
from openai import OpenAI
import importlib.resources as pkg_resources
from repello_agent_wiz import analyzers
import re 

def process_markdown_input(md_text):
        image_line = "![](https://raw.githubusercontent.com/Repello-AI/Agent-Wiz/master/assets/agent_wiz.png)"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        current_datetime_string = now_utc.strftime("%Y-%m-%d %H:%M:%S %Z")
        if not current_datetime_string.endswith("UTC"):
            current_datetime_string = now_utc.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        generated_at_line = f"*Generated at {current_datetime_string}*"

        lines = md_text.splitlines()
        first_line = lines[0]
        rest_of_lines = lines[1:]
        rest_of_text = "\n".join(rest_of_lines)

        md_text_structured = f"{first_line}\n{generated_at_line}\n\n{rest_of_text}"
        md_text_with_image_and_date = image_line + "\n\n" + md_text_structured
        
        final_lines = md_text_with_image_and_date.splitlines()
        corrected_lines = []
        for line in final_lines:
            if re.match(r'^  [-*+] ', line):
                corrected_lines.append('  ' + line) 
            else:
                corrected_lines.append(line)
        return "\n".join(corrected_lines)

def generate_maestro_analysis_report(json_path: str, output_format="pdf"):
    with pkg_resources.files(analyzers).joinpath("maestro.txt").open("r") as f:
        maestro = f.read()

    with pkg_resources.files(analyzers).joinpath("sys_prompt.txt").open("r") as f:
        sys_prompt_template = f.read()

    with open(json_path, "r") as f:
        graph_data = json.load(f)
        framework = graph_data.get("metadata", {}).get("framework", "unknown")

    with open(json_path, "r") as f:
        graph_json = f.read()

    sys_prompt = sys_prompt_template.replace("<MAESTRO>", maestro)
    sys_prompt = sys_prompt.replace("<JSON>", graph_json)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": sys_prompt}],
        temperature=0.3
    )

    report = response.choices[0].message.content.strip()
    if report.startswith("```") and report.endswith("```"):
        report = "\n".join(report.splitlines()[1:-1]).strip()
    md_input_path = f"{framework}_report.md"

    with open(md_input_path, "w", encoding='utf-8') as f:
        f.write(report)

    with open(md_input_path, "r", encoding='utf-8') as f:
        report_md_raw_content = f.read()

    report_md_content = process_markdown_input(report_md_raw_content)

        
    if output_format.lower() == "pdf":
        try:
            import markdown
            from weasyprint import HTML, CSS
            from weasyprint.text.fonts import FontConfiguration
            
            try:
                html_content = markdown.markdown(
                    report_md_content,
                    extensions=['tables', 'fenced_code', 'sane_lists']
                )

                css_style = """
                @page {
                    size: A3 landscape;
                    margin-left: 1.5cm;
                    margin-right: 1.5cm;
                    margin-top: 1.5cm;
                    margin-bottom: 2.5cm;

                     @bottom-center {
                        content: "Page " counter(page) " of " counter(pages); /* The page numbering */
                        font-family: 'Helvetica', 'Arial', sans-serif; /* Optional: Style the footer text */
                        font-size: 9pt;
                        color: #555; /* Dim color for footer */
                        vertical-align: top; /* Align text within the margin box */
                        padding-top: 5pt; /* Space above the page number */
                    }
                }
                @page :first {
                    margin-top: 0;
                }
                body {
                    font-family: 'Helvetica', 'Arial', sans-serif;
                    line-height: 1.4;
                    font-size: 14pt;
                }
                img {
                    /* Prevent horizontal overflow */
                    max-width: 110%;
                   

                    /* Prevent excessive vertical size - Adjust vh value as needed */
                    /* 85vh means max 85% of the page's viewport height */
                    /* This helps prevent images from dominating a page */
                    /* and running into the footer margin */
                    max-height: 85vh;

                    /* Try to keep the image on one page */
                    page-break-inside: avoid;

                    /* Ensure proper block behavior for page breaks */
                    display: block;
                    margin-top: -1em;  /* Add some space above image */
                    margin-left: -1.9cm;
                    margin-right: -1.5cm;
                    margin-bottom: 1em; /* Add some space below image */
                }
                h2, h3, h4, h5, h6 {
                    page-break-after: avoid; 
                    margin-top: 1.5em;
                    margin-bottom: 0.5em;
                }
                table {
                    border-collapse: collapse;
                    width: 100%; 
                    margin-top: 1em;
                    margin-bottom: 1em;
                    page-break-inside: avoid; 
                    font-size: 9pt;
                }
                th, td {
                    border: 1px solid #ccc;
                    padding: 6px;
                    text-align: left;
                    vertical-align: top; 
                    word-wrap: break-word; 
                }
                th {
                    background-color: #f2f2f2;
                    font-weight: bold;
                }
                pre {
                    background-color: #f8f8f8;
                    border: 1px solid #ccc;
                    padding: 10px;
                    overflow-x: auto; 
                    page-break-inside: avoid;
                    white-space: pre-wrap; 
                    word-wrap: break-word;
                }
                code {
                    font-family: monospace;
                    background-color: #f8f8f8;
                    padding: 0.2em 0.4em;
                    border-radius: 3px;
                }
                ul, ol {
                    padding-left: 2em;
                }
                ul ul, ol ol, ul ol, ol ul {
                    margin-left: 1.5em; 
                    padding-left: 1.5em;
                    margin-bottom: 0;
                }
                ul ul ul, ol ol ol /* etc */ {
                    margin-left: 1.5em;
                    padding-left: 1.5em;
                }
                li {
                    margin-bottom: 0.3em;
                }
                """
                font_config = FontConfiguration()
                html = HTML(string=html_content)
                css = CSS(string=css_style, font_config=font_config)
                pdf_output_path = f"{framework}_report.pdf"
                html.write_pdf(pdf_output_path, stylesheets=[css], font_config=font_config)
                print(f"[✓] Saved MAESTRO analysis (PDF) to: {pdf_output_path}")

                print(html_content)

                return pdf_output_path

            except ImportError:
                print("[✗] Error: WeasyPrint or Markdown library not installed. Cannot generate PDF.")
                print("      Please install them: pip install WeasyPrint markdown markdown-include")
        except Exception as e:
                # Catch WeasyPrint-specific errors or other issues
                print(f"[✗] Error generating PDF: {e}")
                print("      Ensure WeasyPrint system dependencies are installed correctly.")
                print("      See: https://doc.weasyprint.org/stable/install.html")

    
    return md_input_path