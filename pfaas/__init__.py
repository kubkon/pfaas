import json
from datetime import timedelta
import marshal
import tempfile
from pathlib import PurePath

class remote_fn:
    def __init__(
            self,
            run_local: bool = False,
            budget: float = 100.0,
            timeout: timedelta = timedelta(minutes=10),
            subnet: str = "devnet-alpha.2",
        ):
        self.run_local = run_local
        self.budget = budget
        self.timeout = timeout
        self.subnet = subnet
        self.tmpdir = tempfile.TemporaryDirectory()

    def __call__(self, func):
        async def inner(*args, **kwargs):
            # Firstly, we'll save the function body to file
            module_path = PurePath(f"{self.tmpdir.name}/gfaas_module")
            with open(module_path, "wb") as f:
                marshal.dump(func.__code__, f)

            # Save input args to files
            saved_args = []
            for i, arg in enumerate(args):
                arg_path = PurePath(f"{self.tmpdir.name}/arg{i}")
                with open(arg_path, "w") as f:
                    json.dump(arg, f)
                saved_args.append(arg_path)

            if self.run_local:
                import types

                # Load func from file
                with open(module_path, "rb") as f:
                    code = marshal.load(f)

                # Load input args
                parsed_args = []
                for arg_path in saved_args:
                    with open(arg_path, "r") as f:
                        arg = json.load(f)
                        parsed_args.append(arg)

                # Invoke
                deser = types.FunctionType(code, globals(), "remote")
                return deser(*parsed_args)

            else:
                from yapapi.runner import Engine, Task, vm
                from yapapi.runner.ctx import WorkContext
                from yapapi.log import enable_default_logger, log_summary

                enable_default_logger()
                package = await vm.repo(
                    image_hash = "74e9cdb5a5aa2c73a54f9ebf109986801fe2d4f026ea7d9fbfcca221",
                    min_mem_gib = 0.5,
                    min_storage_gib = 2.0,
                )
                out_path = PurePath(f"{self.tmpdir.name}/out")

                async def worker(ctx: WorkContext, tasks):
                    async for task in tasks:
                        ctx.send_file(module_path, "/golem/input/func")
                        remote_args = []

                        for (i, arg_path) in enumerate(saved_args):
                            remote_arg = f"/golem/input/arg{i}"
                            ctx.send_file(arg_path, remote_arg)
                            remote_args.append(remote_arg)

                        ctx.run("python", "/golem/runner.py", "/golem/input/func", *remote_args)
                        ctx.download_file("/golem/output/out", out_path)
                        yield ctx.commit()
                        task.accept_task(result=out_path)

                    ctx.log("done")

                init_overhead: timedelta = timedelta(minutes = 3)

                async with Engine(
                    package = package,
                    max_workers = 3,
                    budget = self.budget,
                    timeout = init_overhead + self.timeout,
                    subnet_tag = self.subnet,
                    event_emitter = log_summary(),
                ) as engine:
                    async for progress in engine.map(worker, [Task(data = None)]):
                        print(f"progress={progress}")

                with open(out_path, "r") as f:
                    out = json.load(f)

                return out

        return inner
