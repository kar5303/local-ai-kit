# Abaqus Python scripts (enable only when working on Abaqus scripts)
- Check the Python version from the user (Abaqus 2024+ = Python 3; older = Python 2.7). Ask if unknown.
- Use only modules available in Abaqus Python (abaqus, abaqusConstants, odbAccess, numpy). No pip packages.
- Never run abaqus commands (they use licenses). Write the script; the user runs it.
- Do not open or read *.odb, *.cae, *.inp larger than the user's excerpt. Ask for the needed part.
- Keep units as the user states them. Do not convert units unless asked.
