# boot.py - MHUTEMP PRO

print("================================")
print(" MHUTEMP PRO - BOOT")
print("================================")

try:
    import main
except Exception as e:
    print("ERROR cargando main.py:", e)