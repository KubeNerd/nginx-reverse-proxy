from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "API Stateless Flask!"})


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})

@app.route('/echo', methods=['POST'])
def echo():
    data = request.json
    return jsonify({"received": data})
