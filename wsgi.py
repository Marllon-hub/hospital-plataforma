import eventlet
eventlet.monkey_patch()

from app import app, socketio  # app.py

# Para Gunicorn usar "app"
# (socketio é importado só pra garantir que o SocketIO foi inicializado)