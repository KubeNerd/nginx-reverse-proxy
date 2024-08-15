FROM python:3.9.12-alpine3.15

WORKDIR /usr/app

COPY requirements.txt wsgi.py app.py ./

RUN pip install -r requirements.txt

EXPOSE 5000

CMD ["python3", "wsgi.py"]