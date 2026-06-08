import pandas as pd

def estimate_cost():
    # Định nghĩa cấu hình cho các Tiers
    tiers = {
        "Small": {"services": 10, "log_gb_day": 50, "metrics_eps": 100_000},
        "Medium": {"services": 100, "log_gb_day": 500, "metrics_eps": 1_000_000},
        "Large": {"services": 1000, "log_gb_day": 5000, "metrics_eps": 10_000_000}, # 5TB = 5000 GB
    }

    results = []

    for tier, specs in tiers.items():
        log_gb = specs["log_gb_day"]
        metrics_eps = specs["metrics_eps"]
        
        # ---------------------------------------------------------
        # 1. BUILD (Self-Hosted Open Source Architecture)
        # Giả định giá thuê VM/Cloud resource (AWS) per month
        # ---------------------------------------------------------
        
        # Storage (Logs - Elasticsearch/Loki)
        # Giả sử lưu trữ 30 ngày, giá $0.05 / GB-month (bao gồm compute nhẹ cho storage)
        storage_gb_month = log_gb * 30
        self_hosted_storage_cost = storage_gb_month * 0.05
        
        # Compute (Kafka, Flink, OTel Collectors)
        # 100k EPS cần ~2 VM (4 vCPU, 16GB) = ~$150/tháng
        # Tỉ lệ thuận với số lượng metrics_eps
        compute_units = metrics_eps / 100_000
        self_hosted_compute_cost = compute_units * 150 # $150 per 100k EPS capacity
        
        # Network (Egress/Ingress cross-AZ)
        # Ước tính $0.01 / GB data transfer
        metric_gb_day = (metrics_eps * 100 * 86400) / (1024**3) # 100 bytes per event
        total_transfer_gb_month = (log_gb + metric_gb_day) * 30
        self_hosted_network_cost = total_transfer_gb_month * 0.01
        
        # Total Build Cost
        self_hosted_total = self_hosted_storage_cost + self_hosted_compute_cost + self_hosted_network_cost

        # ---------------------------------------------------------
        # 2. BUY (Datadog SaaS)
        # ---------------------------------------------------------
        
        # Datadog Log Management: $0.10 per ingested GB + $1.70 per 1 million log events retained (simplify to $0.60/GB)
        datadog_log_cost = log_gb * 30 * 0.60
        
        # Datadog Infrastructure (Metrics) 
        # Tương đối: $15 / host. Giả định 1 host sinh ra ~ 1000 metrics EPS -> số host = metrics_eps / 1000
        # (Cách tính tương đối để scale)
        estimated_hosts = specs["services"] * 5 # Giả sử mỗi service có 5 hosts
        datadog_infra_cost = estimated_hosts * 15
        
        # Custom metrics (nếu vượt quá tiêu chuẩn)
        # Mỗi host đi kèm 100 custom metrics, còn lại tính $0.05 / 100 metrics.
        datadog_custom_metric_cost = (metrics_eps / 10_000) * 100 # $100 per 10k EPS custom metrics

        datadog_total = datadog_log_cost + datadog_infra_cost + datadog_custom_metric_cost
        
        results.append({
            "Tier": tier,
            "Services": specs["services"],
            "Build_Storage_Cost": round(self_hosted_storage_cost, 2),
            "Build_Compute_Cost": round(self_hosted_compute_cost, 2),
            "Build_Network_Cost": round(self_hosted_network_cost, 2),
            "Build_Total_Cost": round(self_hosted_total, 2),
            "Datadog_Log_Cost": round(datadog_log_cost, 2),
            "Datadog_Infra_Metric_Cost": round(datadog_infra_cost + datadog_custom_metric_cost, 2),
            "Buy_Total_Cost": round(datadog_total, 2),
        })

    df = pd.DataFrame(results)
    
    print("=== Cost Estimation (Monthly, USD) ===")
    print(df.to_string(index=False))
    
    # Save to CSV for reporting if needed
    df.to_csv("cost_estimation_breakdown.csv", index=False)
    
    return df

if __name__ == "__main__":
    estimate_cost()
