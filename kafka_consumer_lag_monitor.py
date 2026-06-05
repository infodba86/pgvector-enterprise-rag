# ============================================================
# Kafka Consumer Lag Monitoring Script
# Author: Suresh Nadipineni | Senior DBA & AI Data Engineer
# GitHub: github.com/infodba86
# ============================================================

from confluent_kafka.admin import AdminClient
from confluent_kafka import Consumer, KafkaException
from datetime import datetime
import os, json, time

BOOTSTRAP_SERVERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SASL_USERNAME      = os.getenv("KAFKA_SASL_USERNAME", "")
SASL_PASSWORD      = os.getenv("KAFKA_SASL_PASSWORD", "")
LAG_ALERT_THRESHOLD = int(os.getenv("LAG_ALERT_THRESHOLD", "10000"))
MONITOR_INTERVAL   = int(os.getenv("MONITOR_INTERVAL_SEC", "60"))

def get_admin_config():
    cfg = {"bootstrap.servers": BOOTSTRAP_SERVERS}
    if SASL_USERNAME:
        cfg.update({
            "security.protocol"  : "SASL_SSL",
            "sasl.mechanisms"    : "PLAIN",
            "sasl.username"      : SASL_USERNAME,
            "sasl.password"      : SASL_PASSWORD,
        })
    return cfg

def get_consumer_groups(admin):
    result = admin.list_consumer_groups()
    groups = result.result()
    return [g.group_id for g in groups.valid]

def get_group_lag(admin, group_id):
    try:
        offsets = admin.list_consumer_group_offsets([group_id])
        result  = offsets[group_id].result()
        lag_info = []
        for tp, offset_info in result.topic_partition_offsets.items():
            committed = offset_info.offset if offset_info.offset >= 0 else 0
            lag_info.append({
                "group"    : group_id,
                "topic"    : tp.topic,
                "partition": tp.partition,
                "committed": committed,
            })
        return lag_info
    except Exception as e:
        return []

def print_lag_report(all_lag):
    print("\n" + "="*65)
    print(f"  Kafka Consumer Lag Monitor")
    print(f"  Servers : {BOOTSTRAP_SERVERS}")
    print(f"  Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Alert Threshold: {LAG_ALERT_THRESHOLD:,} messages")
    print("="*65)

    alerts = [r for r in all_lag if r.get("lag", 0) > LAG_ALERT_THRESHOLD]
    if alerts:
        print(f"\n  ⚠ ALERTS — {len(alerts)} partitions exceed threshold:")
        for a in alerts:
            print(f"    - {a['group']} | {a['topic']}[{a['partition']}] lag={a.get('lag',0):,}")
    else:
        print("\n  All consumer groups within normal lag range.")

    by_group = {}
    for r in all_lag:
        g = r["group"]
        by_group.setdefault(g, 0)
        by_group[g] += r.get("lag", 0)

    print("\n  GROUP SUMMARY:")
    for g, total in sorted(by_group.items(), key=lambda x: -x[1]):
        status = "ALERT" if total > LAG_ALERT_THRESHOLD else "OK"
        print(f"    [{status:5}] {g:<40} total lag={total:,}")
    print("="*65 + "\n")

def main():
    print(f"Connecting to Kafka: {BOOTSTRAP_SERVERS}")
    cfg   = get_admin_config()
    admin = AdminClient(cfg)
    print("Connected! Starting lag monitoring...\n")

    while True:
        groups  = get_consumer_groups(admin)
        all_lag = []
        for group in groups:
            lag_info = get_group_lag(admin, group)
            all_lag.extend(lag_info)

        print_lag_report(all_lag)

        out = f"kafka_lag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(out, "w") as f:
            json.dump(all_lag, f, indent=2, default=str)

        print(f"Next check in {MONITOR_INTERVAL}s... (Ctrl+C to stop)")
        time.sleep(MONITOR_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
