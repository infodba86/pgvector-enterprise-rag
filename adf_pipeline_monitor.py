# ============================================================
# Azure Data Factory Pipeline Monitoring Script
# Author: Suresh Nadipineni | Senior DBA & AI Data Engineer
# GitHub: github.com/infodba86
# ============================================================

from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient
from datetime import datetime, timedelta, timezone
import json, os

SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "your-subscription-id")
RESOURCE_GROUP  = os.getenv("AZURE_RESOURCE_GROUP",  "your-resource-group")
FACTORY_NAME    = os.getenv("ADF_FACTORY_NAME",      "your-adf-name")
HOURS_BACK      = int(os.getenv("MONITOR_HOURS", "24"))

def get_adf_client():
    credential = DefaultAzureCredential()
    return DataFactoryManagementClient(credential, SUBSCRIPTION_ID)

def get_pipeline_runs(client):
    end_time   = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=HOURS_BACK)
    from azure.mgmt.datafactory.models import RunFilterParameters
    params = RunFilterParameters(last_updated_after=start_time, last_updated_before=end_time)
    runs   = client.pipeline_runs.query_by_factory(RESOURCE_GROUP, FACTORY_NAME, params)
    return runs.value

def analyze_runs(runs):
    summary = {"succeeded": [], "failed": [], "running": [], "cancelled": []}
    for run in runs:
        status = run.status.lower() if run.status else "unknown"
        info = {
            "pipeline"    : run.pipeline_name,
            "run_id"      : run.run_id,
            "start"       : str(run.run_start),
            "end"         : str(run.run_end),
            "duration_sec": run.duration_in_ms / 1000 if run.duration_in_ms else 0,
            "message"     : run.message or ""
        }
        if   status == "succeeded" : summary["succeeded"].append(info)
        elif status == "failed"    : summary["failed"].append(info)
        elif status == "inprogress": summary["running"].append(info)
        else                       : summary["cancelled"].append(info)
    return summary

def print_report(summary):
    print("\n" + "="*60)
    print(f"  ADF Pipeline Monitor — Last {HOURS_BACK} Hours")
    print(f"  Factory : {FACTORY_NAME}")
    print(f"  Report  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print(f"  Succeeded : {len(summary['succeeded'])}")
    print(f"  Failed    : {len(summary['failed'])}")
    print(f"  Running   : {len(summary['running'])}")
    print(f"  Cancelled : {len(summary['cancelled'])}")

    if summary["failed"]:
        print("\n  FAILED PIPELINES:")
        for r in summary["failed"]:
            print(f"    - {r['pipeline']} | {r['start']} | {r['message'][:80]}")

    if summary["running"]:
        print("\n  CURRENTLY RUNNING:")
        for r in summary["running"]:
            print(f"    - {r['pipeline']} | Started: {r['start']}")

    long_runs = [r for r in summary["succeeded"] if r["duration_sec"] > 3600]
    if long_runs:
        print("\n  LONG RUNNING (>1 hour):")
        for r in long_runs:
            print(f"    - {r['pipeline']} | {r['duration_sec']/60:.1f} min")
    print("="*60 + "\n")

def main():
    print(f"Connecting to ADF: {FACTORY_NAME}...")
    client  = get_adf_client()
    runs    = get_pipeline_runs(client)
    summary = analyze_runs(runs)
    print_report(summary)
    out = f"adf_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Report saved: {out}")

if __name__ == "__main__":
    main()
