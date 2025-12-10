#!/bin/bash

# =============================================================================
# Docker Build Script
# =============================================================================
# Build Docker images with caching and versioning
# Usage: ./scripts/docker/docker-build.sh [--no-cache] [--tag TAG]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
NO_CACHE=false
CUSTOM_TAG=""
PROJECT_NAME="app"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --tag)
            CUSTOM_TAG="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--no-cache] [--tag TAG]"
            echo "  --no-cache    Build without using cache"
            echo "  --tag TAG     Custom tag for the image"
            echo "  -h, --help    Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Helper functions
print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Get git info for tagging
get_git_info() {
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        GIT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "")
    else
        GIT_COMMIT="unknown"
        GIT_BRANCH="unknown"
        GIT_TAG=""
    fi
}

# Cleanup old images
cleanup_old_images() {
    print_status "Cleaning up old images..."

    DANGLING_IMAGES=$(docker images -f "dangling=true" -q)
    if [ ! -z "$DANGLING_IMAGES" ]; then
        docker rmi $DANGLING_IMAGES 2>/dev/null || true
        print_success "Removed dangling images"
    fi
}

# Build image
build_image() {
    local cache_flag=""
    if [ "$NO_CACHE" = true ]; then
        cache_flag="--no-cache"
        print_warning "Building without cache"
    fi

    # Generate tag
    local tag_name
    if [ ! -z "$CUSTOM_TAG" ]; then
        tag_name="$CUSTOM_TAG"
    elif [ ! -z "$GIT_TAG" ]; then
        tag_name="$GIT_TAG"
    else
        tag_name="$GIT_BRANCH-$GIT_COMMIT"
    fi

    # Build arguments
    local build_args=""
    build_args="--build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    build_args="$build_args --build-arg GIT_COMMIT=$GIT_COMMIT"
    build_args="$build_args --build-arg GIT_BRANCH=$GIT_BRANCH"

    print_status "Building image: $PROJECT_NAME:$tag_name"
    print_status "Git commit: $GIT_COMMIT"
    print_status "Git branch: $GIT_BRANCH"

    docker build \
        $cache_flag \
        $build_args \
        -t "$PROJECT_NAME:$tag_name" \
        -t "$PROJECT_NAME:latest" \
        .

    if [ $? -eq 0 ]; then
        print_success "Successfully built image: $PROJECT_NAME:$tag_name"
        docker images | grep "$PROJECT_NAME" | head -5
        return 0
    else
        print_error "Failed to build image"
        return 1
    fi
}

# Validate environment
validate_environment() {
    print_status "Validating environment..."

    if ! docker info >/dev/null 2>&1; then
        print_error "Docker is not running"
        return 1
    fi

    if [ ! -f "Dockerfile" ]; then
        print_error "Dockerfile not found in current directory"
        return 1
    fi

    print_success "Environment validation passed"
    return 0
}

# Main execution
main() {
    print_status "Starting Docker build process..."

    cd "$(dirname "$0")/../.."

    if ! validate_environment; then
        exit 1
    fi

    get_git_info

    if build_image; then
        cleanup_old_images
        print_success "Build process completed successfully!"
        print_status "You can now run: docker-compose up"
        exit 0
    else
        print_error "Build process failed!"
        exit 1
    fi
}

main "$@"
