import os, socket
from app import app

host_address = socket.gethostbyname(socket.gethostname())

if __name__ == '__main__':
    app.run(host=host_address, port=os.getenv("PORT", 5000), debug=False)