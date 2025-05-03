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

def generate_maestro_analysis_report(json_path: str):
    with pkg_resources.files(analyzers).joinpath("maestro.txt").open("r") as f:
        maestro = f.read()

    with pkg_resources.files(analyzers).joinpath("sys_prompt.txt").open("r") as f:
        sys_prompt_template = f.read()

    with pkg_resources.files(analyzers).joinpath("css.txt").open("r") as f:
        css_style = f.read()

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
    md_input_path = f"AgentChat_report.md"

    with open(md_input_path, "w", encoding='utf-8') as f:
        f.write(report)

    with open(md_input_path, "r", encoding='utf-8') as f:
        report_md_raw_content = f.read()

    report_md_content = process_markdown_input(report_md_raw_content)

        
    try:
        import markdown
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration
        
        try:
            html_content = markdown.markdown(
                report_md_content,
                extensions=['tables', 'fenced_code', 'sane_lists']
            )

            font_config = FontConfiguration()
            html = HTML(string=html_content)
            css = CSS(string=css_style, font_config=font_config)
            pdf_output_path = f"{framework}_report.pdf"
            html.write_pdf(pdf_output_path, stylesheets=[css], font_config=font_config)
            
            print(f"[✓] Saved MAESTRO analysis (PDF) to: {pdf_output_path}")

            return pdf_output_path

        except ImportError:
            print("[✗] Error: WeasyPrint or Markdown library not installed. Cannot generate PDF.")
    except Exception as e:
            print(f"[✗] Error generating PDF: {e}")

    return md_input_path
