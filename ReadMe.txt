1. Environment Preparation

Since you aren't using the venv folder from the previous session, we need to ensure your environment is ready.

    Open Terminal: Navigate to your project root: riz@Mac SAIS_platform %.

    Create/Activate Virtual Environment:
    Bash
--------------------------------------------------------------------------------------------
    python3 -m venv venv
    source venv/bin/activate
--------------------------------------------------------------------------------------------

    Install Dependencies: Install the required packages listed in your requirements.txt.
    Bash
--------------------------------------------------------------------------------------------
    pip install -r requirements.txt
--------------------------------------------------------------------------------------------

2. Infrastructure Setup (Docker)

Your project includes a docker-compose.yml and mosquitto.conf, which are likely used for the MQTT broker (required for the ESL tags to communicate).

    Start Containers: Run Docker in the background.
    Bash

--------------------------------------------------------------------------------------------
    docker-compose up -d
--------------------------------------------------------------------------------------------

    Verify MQTT: Ensure the Mosquitto broker is running so the mqtt_worker.py can connect later.

3. Database and Migrations

Since you have a db.sqlite3 file and several migration files, we need to ensure the schema is current.

    Apply Migrations:
    Bash
--------------------------------------------------------------------------------------------
    python manage.py migrate
--------------------------------------------------------------------------------------------

    Collect Static Files: Ensure the admin CSS and custom JS are gathered.
    Bash

--------------------------------------------------------------------------------------------
    python manage.py collectstatic --noinput
--------------------------------------------------------------------------------------------

4. Launching the Application

You have a shell script Start_SAIS.sh which likely handles the simultaneous launch of the web server and the background workers.
Option A: Using the Script (Recommended)

Make the script executable and run it:
Bash

--------------------------------------------------------------------------------------------
chmod +x Start_SAIS.sh
./Start_SAIS.sh
--------------------------------------------------------------------------------------------



******************************************************************************************
What you MUST DO (Every restart)

    Navigate to the folder: cd path/to/SAIS_platform

    Activate the environment: source venv/bin/activate

    Start Docker: docker-compose up -d (If Docker isn't set to start automatically with your Mac).

    Run the script: ./Start_SAIS.sh