#!/usr/bin/env bash
# Create the AWS VM JARVIS runs on — one command, idempotent.
#
#   infra/scripts/aws-launch.sh            # region from AWS_REGION or ap-south-1 (Mumbai)
#
# Needs `aws configure` done once (an IAM access key with EC2 permissions). Picks the
# instance that is *actually free* on a post-2025 AWS account: **t4g.small** — 2 vCPU /
# 2 GB ARM, 750 hours a month at $0 through 31 Dec 2026, then ≈₹1,000/mo. The deploy
# script adds swap, which is what makes 2 GB enough for this stack. Everything else it
# creates (30 GB gp3 disk, a public IPv4) costs a few dollars a month against the
# sign-up credits.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
NAME="${NAME:-jarvis-x}"
TYPE="${INSTANCE_TYPE:-t4g.small}"
DISK_GB="${DISK_GB:-30}"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/${NAME}-${REGION}.pem}"
export AWS_DEFAULT_REGION="$REGION"

aws sts get-caller-identity >/dev/null 2>&1 || { echo "run: aws configure   (needs an IAM access key)" >&2; exit 1; }

echo "▸ key pair $NAME"
if ! aws ec2 describe-key-pairs --key-names "$NAME" >/dev/null 2>&1; then
  mkdir -p "$(dirname "$KEY_FILE")"
  aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 \
    --query KeyMaterial --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "  private key saved to $KEY_FILE"
elif [ ! -f "$KEY_FILE" ]; then
  echo "  key pair $NAME exists in AWS but $KEY_FILE is missing; delete it in the console or set KEY_FILE" >&2
  exit 1
fi

echo "▸ security group $NAME (22, 80, 443)"
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NAME" Name=vpc-id,Values="$VPC" \
       --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME" --description "JARVIS X" --vpc-id "$VPC" \
         --query GroupId --output text)
  for port in 22 80 443; do
    aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port "$port" --cidr 0.0.0.0/0 >/dev/null
  done
fi

echo "▸ Ubuntu 22.04 arm64 AMI"
ARCH=$([[ "$TYPE" == t4g* || "$TYPE" == m7g* || "$TYPE" == c7g* ]] && echo arm64 || echo amd64)
AMI=$(aws ec2 describe-images --owners 099720109477 \
        --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-${ARCH}-server-*" Name=state,Values=available \
        --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)

EXISTING=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values="$NAME" Name=instance-state-name,Values=pending,running,stopped \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo None)
if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
  echo "▸ instance $EXISTING already exists; starting it if stopped"
  aws ec2 start-instances --instance-ids "$EXISTING" >/dev/null 2>&1 || true
  ID="$EXISTING"
else
  echo "▸ launching $TYPE ($ARCH), ${DISK_GB} GB"
  ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name "$NAME" \
        --security-group-ids "$SG" \
        --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$DISK_GB,\"VolumeType\":\"gp3\"}}]" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
        --query 'Instances[0].InstanceId' --output text)
fi

aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "$IP" > var/aws-ip 2>/dev/null || true

cat <<EOF

  ✓ $ID is running in $REGION
    public IP : $IP
    ssh       : ssh -i $KEY_FILE ubuntu@$IP

  Next:
    1. DuckDNS → set your name's IP to $IP
    2. make deploy HOST=ubuntu@$IP KEY=$KEY_FILE DOMAIN=<name>.duckdns.org

  The IP changes if the instance is ever stopped; \`aws ec2 allocate-address\` pins one for ~\$3.6/mo.
EOF
