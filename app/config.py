import os


class Settings:
    # Server
    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = int(os.getenv("SERVER_PORT", "8111"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # VirtualBox VM
    vm_name: str = os.getenv("VM_NAME", "Win11VM")
    vm_username: str = os.getenv("VM_USERNAME", "marvin-box")
    vm_password: str = os.getenv("VM_PASSWORD", "123123")

    # Paths
    shared_folder_host: str = os.getenv("SHARED_FOLDER_HOST", "/home/data/vbox_shared")
    shared_folder_vm: str = os.getenv("SHARED_FOLDER_VM", r"Z:\shared")
    cxcalc_path: str = os.getenv(
        "CXCALC_PATH",
        r"C:\Program Files (x86)\ChemAxon\MarvinBeans\bin\cxcalc.bat",
    )

    # Timeouts (seconds)
    command_timeout: int = int(os.getenv("COMMAND_TIMEOUT", "600"))


settings = Settings()
