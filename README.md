# PP1

## Base de datos MySQL

La aplicación usa MySQL 8.0 o posterior y `mysql-connector-python`.

1. Instalar las dependencias: `pip install -r requirements.txt`.
2. Ejecutar `DBcreateScript.txt` con un usuario MySQL que pueda crear la base
   de datos.
3. Completar host, puerto, usuario y contraseña en `DB.ini`. El nombre de la
   base configurado allí debe coincidir con el nombre creado por el script.
4. Iniciar la aplicación como de costumbre.

La aplicación abre conexiones bajo demanda; no intenta conectarse durante el
arranque. Para copiar los datos actuales de `placeholder.json` a una base vacía,
completá `DB.ini` y ejecutá `python -m python.import_placeholder`. La importación
conserva los IDs y relaciones, es transaccional y se cancela si encuentra datos
previos para evitar duplicados. El archivo JSON no se modifica.
