import pickle, json, subprocess, hashlib
from . import Signer

class Deployer:

    DEPLOY = 0
    SEND = 1
    SKIP_START = 2
    SKIP_END = 3

    def __init__(
        self,
        rpc: str,
        signer: Signer,
        contracts: list,
        is_legacy: bool,
        debug=False,
        cache_path="cache",
    ):
        print("#####")
        print(f"# RPC: `{rpc}`")

        self.rpc = rpc
        self.contracts = {}
        self.addresses = {}
        self.contract_signatures = {}

        # Load from cache if it exists
        self.cache_path = (
            cache_path + "/deploy_" + hashlib.sha256(rpc.encode()).hexdigest()[:8]
        )
        self.load_from_cache(self.cache_path)

        # Add/Replace cached values
        self.add_contracts(contracts)
        self.signer = signer
        self.debug = debug

        if is_legacy:
            self.is_legacy = "--legacy"
        print("#####\n")

    ###########################
    # Helpers
    ###########################

    def print(self, sigs: bool = False):
        print(f"\n##\n {self.addresses}")
        if sigs:
            print(self.contracts)
            print(self.contract_signatures)

    def _handle_arg(self, arg: str) -> str:

        if arg.startswith("$"):
            contract_label = arg[1:]
            arg = f"{self.addresses[contract_label]}"

        elif arg.startswith("#PUB"):
            arg = f"{self.signer.pub()}"

        return arg


    ###########################
    # Cache
    ###########################

    def load_from_cache(self, cache_path):
        try:
            deployer = Deployer.load(cache_path)
            print(f"# Loading cache at `{cache_path}`")
            self.contracts = deployer.contracts
            self.addresses = deployer.addresses
            self.contract_signatures = deployer.contract_signatures

        except FileNotFoundError:
            print(f"# Starting cache at `{cache_path}`")
            pass

    def load(cache_path):
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    def save(self):
        with open(self.cache_path, "wb") as f:
            pickle.dump(self, f)

    ###########################
    # Contract loading
    ###########################

    def load_contract_signatures(self, contract_label: str, contract_path: str):
        """
            Reads ABI from out/ folder generated by foundry and loads out function names and signatures
        """
        contract_file_path, contract_name = contract_path.split(":")

        out = {}
        for chunk in contract_file_path.split("/"):
            if chunk.endswith(".sol"):
                with open(f"out/" + chunk + "/" + contract_name + ".json") as f:
                    out = json.load(f)
                    break

        abi = out["abi"]

        signatures = {}
        for obj in abi:
            if obj["type"] == "function":

                # Get inputs
                inputs = []
                for inp in obj["inputs"]:
                    inputs.append(inp["type"])
                inputs = ",".join(inputs)

                # Get Name
                func_name = obj["name"]
                signature = "{}({})".format(func_name, inputs)

                if contract_path not in self.contract_signatures:
                    self.contract_signatures[contract_path] = {}

                self.contract_signatures[contract_path][func_name] = signature

    def add_contracts(self, contracts: [tuple]):
        """
        Example:
            contracts = [
                ("CONTRACT_1_LABEL", "src/Contract1.sol:ContractName1", "0x1111111111111111111111111111111111111111"),
                ("CONTRACT_2_LABEL", "src/Contract2.sol:ContractName2")
            ]
        """
        for contract in contracts:
            if contract[1] != "":
                self.contracts[contract[0]] = contract[1]
                self.load_contract_signatures(contract[0], contract[1])

            if len(contract) == 3:
                self.addresses[contract[0]] = contract[2]

    ###########################
    # OS execution
    ###########################

    def run(self, cmd: str):
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
        result = (proc.stdout.read()).decode()
        if self.debug:
            print(
                f"""
            # `{cmd}`
            ##
            $ `{result}`
            --
            """
            )
        proc.wait()

        if not proc.returncode == 0:
            self.save()
            print(f"FAILED:\n{cmd}\n\n###\n\n{result}\n\r")
            self.print()
            exit(1)

        return result

    ###########################
    # Foundry Calls
    ###########################

    def deploy(self, contract_label: str, args: str) -> str:
        """
        Calls `$ forge create`
        """

        # Skips deployment if there's an address cached for this contract label
        if contract_label in self.addresses:
            print(
                f"Skipping ${contract_label} deployment. Has address: {self.addresses[contract_label]}"
            )
            return self.addresses[contract_label]

        contract_path = self.contracts[contract_label]

        # Stringify arguments
        const = ""
        for arg in args:
            const += f"--constructor-args {self._handle_arg(arg)} "

        print(f"Deploying | ${contract_label}...")

        # Call `forge create`
        result = self.run(
            f"forge create {self.rpc} {self.is_legacy} {self.signer.get()} {contract_path} {const}"
        )

        # Store deployed address
        address = ""
        for line in result.splitlines():
            if "Deployed to: " in line:
                address = line[-42:]
                break

        if address == "":
            raise ValueError("address not sucessfully parsed")

        self.addresses[contract_label] = address

        return address

    def send(self, contract_label: str, address: str, _args: str) -> str:
        """
        Calls `$ cast send`
        """

        contract_path = self.contracts[contract_label]

        # Get function signature
        function_name = _args[0]
        if function_name not in self.contract_signatures[contract_path]:
            raise ValueError(f"{function_name} does not exist in {self.contract_path}")

        _args[0] = '"' + self.contract_signatures[contract_path][function_name] + '"'

        print(f"Sending   | ${contract_label} {function_name}(...) ")

        # Stringify arguments
        args = ""
        for index, arg in enumerate(_args):
            args += f" {self._handle_arg(arg)} "

        self.run(
            f"cast send {address} {self.rpc} {self.is_legacy} {self.signer.get()} {args}"
        )

    ###########################
    # Action Flow
    ###########################

    def path(self, path: list):
        """
        Example:

            path = [
                (Deployer.SKIP_START,0,0),
                (Deployer.DEPLOY, "CONTRACT_0_LABEL", ["Arg1", "Arg2", "1ether"]),
                (Deployer.DEPLOY, "CONTRACT_1_LABEL", ["Arg1", "Arg2", "1ether"]),
                (Deployer.SKIP_END,0,0),

                (Deployer.SEND, "CONTRACT_1_LABEL",   ["ContractMethodName", "9999999999", "00"*32, "00"*32, "0"]),

                (Deployer.DEPLOY, "CONTRACT_2_LABEL", ["Arg1", "Arg2", "12ether"])
            ]

        Will skip the first Deploy and execute the rest, one after the other.
        """
        skipping = False
        for (action, contract_contract_label, arguments) in path:

            if action == Deployer.SKIP_START:
                skipping = True
            elif action == Deployer.SKIP_END:
                skipping = False
                continue

            if skipping:
                continue
            elif action == Deployer.SEND:
                self.send(
                    contract_contract_label,
                    self.addresses[contract_contract_label],
                    arguments,
                )
            elif action == Deployer.DEPLOY:
                self.deploy(contract_contract_label, arguments)

        self.save()
        self.print()
