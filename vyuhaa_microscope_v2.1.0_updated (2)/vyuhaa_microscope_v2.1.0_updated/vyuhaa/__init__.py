"""The Vyuhaa Microscope Server Package.

The Vyuhaa Microscope Server is a LabThings-FastAPI server. All hardware
interfaces, and almost all functionality exposed over HTTP is done using ``Things``.

On the microscope the server is started by systemd. It can be controlled using commands
such as ``ofm restart``, ``ofm start``, and ``ofm stop``.

The server can also be run in simulation mode from the terminal. In the root directory
of the repository the server can be started with run:

.. code-block:: bash

    vyuhaa-microscope-server --fallback -c ./ofm_config_simulation.json


Note: If you are using Windows, we advise running this command in PowerShell.
If it doesn't work this, might be a permission issue on managed machines,
in which case try executing the command in a CMD terminal.
"""
