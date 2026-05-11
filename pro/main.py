# main.py - MHUTEMP PRO LAUNCHER

import time
import machine

print("================================")
print(" MHUTEMP PRO - MAIN")
print("================================")

try:
    import app

    if hasattr(app, "main"):
        print("Ejecutando app.main()...")
        app.main()
    else:
        print("ERROR: app.py no tiene función main()")

except Exception as e:
    print("ERROR ejecutando app.py:", e)

    try:
        with open("app_error.log", "w") as f:
            f.write(str(e))
    except:
        pass

    print("Reinicio en 10 segundos...")
    time.sleep(10)
    machine.reset()