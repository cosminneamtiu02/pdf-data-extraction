# Terraform configuration — structure and conventions only.
# Cloud-agnostic. No real resources. No provider configured.
#
# To use:
# 1. Choose a cloud provider and add its provider block
# 2. Uncomment the backend configuration in environments/*/backend.tf
# 3. Create modules in modules/ for your infrastructure
# 4. Reference modules from environments/*/main.tf

terraform {
  required_version = ">= 1.5.0"
}
