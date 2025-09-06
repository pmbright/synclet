#!/bin/bash
# Installation script for Synclet on Rocky Linux

set -e

echo "Installing Synclet..."

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root" 
   exit 1
fi

# Install Python 3 and pip if not present
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo "Installing Python 3..."
    dnf install -y python3 python3-pip
fi

# Install MariaDB client libraries
echo "Installing MariaDB client libraries..."
dnf install -y mariadb-devel gcc python3-devel

# Create synclet user
echo "Creating synclet user..."
if ! id -u synclet &> /dev/null; then
    useradd -r -s /bin/false synclet
fi

# Set up application directory
APP_DIR="/opt/synclet"
echo "Setting up application in $APP_DIR..."

# Create directory if it doesn't exist
mkdir -p $APP_DIR

# Copy application files
cp -r . $APP_DIR/

# Create logs directory
mkdir -p $APP_DIR/logs

# Set ownership
chown -R synclet:synclet $APP_DIR

# Create virtual environment
echo "Creating Python virtual environment..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/synclet.service << 'EOF'
[Unit]
Description=Synclet - Magento to QuickBooks Sync
After=network.target mariadb.service

[Service]
Type=simple
User=synclet
Group=synclet
WorkingDirectory=/opt/synclet
Environment="PATH=/opt/synclet/venv/bin"
ExecStart=/opt/synclet/venv/bin/python /opt/synclet/src/synclet.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Configure the application by editing: $APP_DIR/config/config.yaml"
echo "2. Create the database using: mysql -u root -p < $APP_DIR/scripts/create_db.sql"
echo "3. Test the installation: cd $APP_DIR && venv/bin/python src/synclet.py --once"
echo "4. Enable the service: systemctl enable synclet"
echo "5. Start the service: systemctl start synclet"
