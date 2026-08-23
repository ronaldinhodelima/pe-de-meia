"""Inicializacao do worker quando servido pelo Gunicorn em producao."""
import threading

from app import app, run_migration, scheduler_loop


run_migration()
threading.Thread(target=scheduler_loop, daemon=True, name="sync-scheduler").start()
