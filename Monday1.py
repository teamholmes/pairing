import subprocess
import argparse
import yaml
import json
import time
import copy
import os
from datetime import datetime

RESOURCE_VARIATIONS = [
    {"cpu_request": "50m", "cpu_limit": "100m", "istio_request": "10m", "istio_limit": "50m"},
    {"cpu_request": "100m", "cpu_limit": "200m", "istio_request": "20m", "istio_limit": "100m"},
    {"cpu_request": "200m", "cpu_limit": "400m", "istio_request": "50m", "istio_limit": "200m"},
]

def run_kubectl_get(namespace, deployment_name):
    cmd = ["kubectl", "get", "deployment", deployment_name, "-n", namespace, "-o", "yaml"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return yaml.safe_load(result.stdout)

def run_kubectl_apply(deployment_yaml):
    process = subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml.dump(deployment_yaml), text=True)
    if process.returncode != 0:
        raise RuntimeError("Failed to apply updated deployment.")

def remove_affinity_spec(deployment):
    spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    spec.pop("affinity", None)

def update_resources(deployment, container_name, cpu_request, cpu_limit, istio_request, istio_limit):
    containers = deployment["spec"]["template"]["spec"]["containers"]
    for container in containers:
        if container["name"] == container_name:
            container["resources"] = {
                "requests": {"cpu": cpu_request},
                "limits": {"cpu": cpu_limit}
            }

    annotations = deployment["spec"]["template"]["metadata"].get("annotations", {})
    annotations["proxy.istio.io/config"] = json.dumps({
        "proxyMetadata": {
            "ISTIO_CPU_REQUEST": istio_request,
            "ISTIO_CPU_LIMIT": istio_limit
        }
    })
    deployment["spec"]["template"]["metadata"]["annotations"] = annotations

def wait_for_ready(namespace, deployment_name, timeout=300):
    start = time.time()
    while True:
        time.sleep(2)
        cmd = ["kubectl", "get", "deployment", deployment_name, "-n", namespace, "-o", "json"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        deployment_status = json.loads(result.stdout)
        desired = deployment_status["status"].get("replicas", 0)
        available = deployment_status["status"].get("availableReplicas", 0)
        if desired == available and desired > 0:
            return time.time() - start
        if time.time() - start > timeout:
            raise TimeoutError("Deployment did not become ready in time.")

def restore_deployment(original_yaml):
    run_kubectl_apply(original_yaml)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", default="results.json")
    args = parser.parse_args()

    namespace = args.namespace
    deployment_name = args.deployment
    container_name = args.container
    output_file = args.output

    original = run_kubectl_get(namespace, deployment_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"original_{deployment_name}_{timestamp}.yaml", "w") as f:
        yaml.dump(original, f)

    results = []
    for variation in RESOURCE_VARIATIONS:
        print(f"Testing with CPU request={variation['cpu_request']} limit={variation['cpu_limit']}")
        modified = copy.deepcopy(original)
        remove_affinity_spec(modified)
        update_resources(modified, container_name,
                         variation["cpu_request"], variation["cpu_limit"],
                         variation["istio_request"], variation["istio_limit"])

        run_kubectl_apply(modified)
        try:
            duration = wait_for_ready(namespace, deployment_name)
        except TimeoutError:
            duration = None

        results.append({
            "variation": variation,
            "ready_time_seconds": duration
        })

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    best = min([r for r in results if r["ready_time_seconds"] is not None],
               key=lambda x: x["ready_time_seconds"], default=None)

    if best:
        print(f"\nBest resource configuration:\n{json.dumps(best, indent=2)}")
    else:
        print("No successful configuration found.")

    print("\nRestoring original deployment...")
    restore_deployment(original)
    print("Done.")

if __name__ == "__main__":
    main()
