#!/usr/bin/env bash
# Start Apache Jena Fuseki in Docker with an in-memory dataset named /ds.
#
# The entrypoint is bypassed by calling the binary directly — this is required
# for the --update and --mem flags to work correctly on ARM64 (Apple Silicon).
# As a side effect, ADMIN_PASSWORD is never processed; the password is whatever
# the default in shiro.ini is. Find it with:
#
#   docker exec jena-fuseki grep admin /fuseki/shiro.ini
#
# Set SPARQL_PASSWORD in your .env to that value.
set -e

PORT="${MCP_PORT:-3030}"

echo "Starting Apache Jena Fuseki on port $PORT ..."
echo "  Admin UI : http://localhost:$PORT"
echo "  SPARQL   : http://localhost:$PORT/ds/sparql"
echo "  Update   : http://localhost:$PORT/ds/update"
echo "  Dataset  : /ds (in-memory, resets on container restart)"
echo ""
echo "After startup, retrieve the admin password with:"
echo "  docker exec jena-fuseki grep admin /fuseki/shiro.ini"
echo ""

docker run --rm -d \
  --name jena-fuseki \
  -p "${PORT}:3030" \
  stain/jena-fuseki \
    /jena-fuseki/fuseki-server --update --mem /ds

echo "Fuseki started (container: jena-fuseki)"
