import server
import thread
import time
import subprocess

def runServer():
    server.run()

thread.start_new(runServer, ())
time.sleep(3)
#webbrowser.open("http://localhost:8000/Portal%7EPortada_9ada.html")
subprocess.call(r"win32\prism\prism.exe -id cdpedia@python.com.ar -uri http://localhost:8000/Portal%7EPortada_9ada.html -status off -location off -scrollbars on".split())
print "terminado, saliendo."
