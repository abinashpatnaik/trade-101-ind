#!/bin/bash
# setup_vm.sh — One-command setup for Multi-Market Trading Agent on Ubuntu 22.04
# Run as: bash setup_vm.sh
set -e

echo "=== Multi-Market Trading Agent — Cloud VM Setup ==="

# 1. Update system
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Docker + Docker Compose
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER

# 3. Install Docker Compose standalone (v2)
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 4. Create agent directory
mkdir -p ~/trading-agent
cd ~/trading-agent

echo ""
echo "=== Setup complete! Next steps: ==="
echo "1. Upload your trading agent files to ~/trading-agent/"
echo "2. Copy .env.example to .env and fill in your credentials:"
echo "   cp .env.example .env && nano .env"
echo "3. Start the agent:"
echo "   docker-compose up -d"
echo "4. Check logs:"
echo "   docker-compose logs -f trading-agent"
