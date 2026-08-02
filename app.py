import gradio as gr
import pandas as pd
import tempfile
import os
from datetime import datetime
from processing import process_reservations

# Bundled city reference file — committed to the Space alongside app.py.
# To add a new property: append a row to property_to_city.csv with the exact
# normalized property name (as it appears in the output "Property" column)
# and the city, then redeploy the Space.
CITY_REF_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "property_to_city.csv")

REQUIRED_CHECKOUT = {'LISTING', 'CHECK-OUT DATE', 'CHECK-OUT TIME'}
REQUIRED_CHECKIN  = {'LISTING', 'CHECK-IN DATE',  'CHECK-IN TIME'}


def _load_city_ref() -> dict:
    if not os.path.exists(CITY_REF_CSV):
        return {}
    try:
        df = pd.read_csv(CITY_REF_CSV)
        if 'Property' not in df.columns or 'City' not in df.columns:
            return {}
        return {
            str(r['Property']).strip(): str(r['City']).strip()
            for _, r in df.iterrows()
            if str(r['City']).strip() and str(r['City']).strip().lower() != 'nan'
        }
    except Exception:
        return {}


def run(checkout_path: str, checkin_path: str):
    if not checkout_path or not checkin_path:
        return None, "Please upload both CSV files before processing."

    try:
        df_checkout = pd.read_csv(checkout_path)
        df_checkout.columns = df_checkout.columns.str.strip()
    except Exception as e:
        return None, f"Could not read Check-out CSV: {e}"

    missing = REQUIRED_CHECKOUT - set(df_checkout.columns)
    if missing:
        return None, f"Check-out CSV is missing required columns: {', '.join(sorted(missing))}"

    try:
        df_checkin = pd.read_csv(checkin_path)
        df_checkin.columns = df_checkin.columns.str.strip()
    except Exception as e:
        return None, f"Could not read Check-in CSV: {e}"

    missing = REQUIRED_CHECKIN - set(df_checkin.columns)
    if missing:
        return None, f"Check-in CSV is missing required columns: {', '.join(sorted(missing))}"

    try:
        city_seed = _load_city_ref()
        df_out = process_reservations(df_checkout, df_checkin, city_seed)
    except Exception as e:
        return None, f"Processing error: {e}"

    # Write to a per-request temp directory so concurrent users don't collide
    today = datetime.today().strftime("%Y-%m-%d")
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, f"guesty_res_{today}.csv")
    df_out.to_csv(out_path, index=False)

    n_rows = len(df_out)
    n_to = (df_out['T/O'] == 'yes').sum()
    return out_path, f"Done — {n_rows} rows, {n_to} turnovers."


with gr.Blocks(title="RC Reservation Processor") as demo:
    gr.Markdown("## RC Reservation Processor")
    gr.Markdown(
        "Upload the two Guesty CSV exports below, then click **Process**. "
        "The housekeeping schedule will be ready to download."
    )

    with gr.Row():
        checkout_upload = gr.File(
            label="Check-out CSV",
            file_types=[".csv"],
            type="filepath",
        )
        checkin_upload = gr.File(
            label="Check-in CSV",
            file_types=[".csv"],
            type="filepath",
        )

    run_btn     = gr.Button("Process", variant="primary")
    status_box  = gr.Textbox(label="Status", interactive=False)
    output_file = gr.File(label="Download result")

    run_btn.click(
        fn=run,
        inputs=[checkout_upload, checkin_upload],
        outputs=[output_file, status_box],
    )

try:
    _user = os.environ["APP_USER"]
    _pass = os.environ["APP_PASS"]
except KeyError as e:
    raise SystemExit(
        f"Missing environment variable {e}. "
        "Set APP_USER and APP_PASS as Space secrets before launching."
    )

demo.launch(
    auth=(_user, _pass),
    auth_message="Enter the credentials provided to you.",
)
