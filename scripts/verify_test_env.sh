#!/bin/bash

# Script to verify test environment is properly set up
# This checks that all required dependencies are installed

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "Verifying test environment setup..."

# Check if test containers are running
if ! docker-compose -f docker-compose.test.yml ps | grep -q "web-test.*Up"; then
    echo -e "${RED}✗ web-test container is not running${NC}"
    echo "Run: docker-compose -f docker-compose.test.yml up -d"
    exit 1
fi

echo "Checking installed packages in web-test container..."

# Check pytest
if docker-compose -f docker-compose.test.yml exec -T web-test python -c "import pytest; print(f'✓ pytest {pytest.__version__}')" 2>/dev/null; then
    echo -e "${GREEN}✓ pytest is installed${NC}"
else
    echo -e "${RED}✗ pytest is not installed${NC}"
    echo "Run: docker-compose -f docker-compose.test.yml exec web-test pip install -r requirements-test.txt"
    exit 1
fi

# Check Django
if docker-compose -f docker-compose.test.yml exec -T web-test python -c "import django; print(f'✓ Django {django.__version__}')" 2>/dev/null; then
    echo -e "${GREEN}✓ Django is installed${NC}"
else
    echo -e "${RED}✗ Django is not installed${NC}"
    echo "Run: docker-compose -f docker-compose.test.yml exec web-test pip install -r requirements.txt"
    exit 1
fi

# Check pytest-django
if docker-compose -f docker-compose.test.yml exec -T web-test python -c "import pytest_django; print('✓ pytest-django')" 2>/dev/null; then
    echo -e "${GREEN}✓ pytest-django is installed${NC}"
else
    echo -e "${RED}✗ pytest-django is not installed${NC}"
    exit 1
fi

# Check pytest-cov
if docker-compose -f docker-compose.test.yml exec -T web-test python -c "import pytest_cov; print('✓ pytest-cov')" 2>/dev/null; then
    echo -e "${GREEN}✓ pytest-cov is installed${NC}"
else
    echo -e "${RED}✗ pytest-cov is not installed${NC}"
    exit 1
fi

# Check database connection
echo "Checking database connection..."
if docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py shell -c "from django.db import connection; cursor = connection.cursor(); cursor.execute('SELECT 1'); print('✓ Database connection working')" 2>/dev/null; then
    echo -e "${GREEN}✓ Database connection is working${NC}"
else
    echo -e "${RED}✗ Database connection failed${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Test environment is properly configured!${NC}"
echo ""
echo "You can now run:"
echo "  • make test"
echo "  • make test-coverage"
echo "  • docker-compose -f docker-compose.test.yml exec web-test pytest"