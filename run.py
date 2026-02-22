"""
Health check wrapper for Cloud Run
Runs the bot + a health check server in parallel
"""
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import your main bot
import main as bot_main


class HealthHandler(BaseHTTPRequestHandler):
    """Simple health check endpoint."""
    
    def do_GET(self):
        """Respond to health checks."""
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'FleetFuel Bot Running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress HTTP logs."""
        pass


def run_health_server():
    """Run health check server on PORT."""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"✅ Health check server running on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    # Start health check server in background thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Run the actual bot (this blocks forever)
    bot_main.main()
