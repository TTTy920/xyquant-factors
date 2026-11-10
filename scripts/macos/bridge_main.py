"""Entrypoint for both the stdio server and its durable workers."""

import os
import runpy

os.umask(0o077)
# -I keeps project modules and user site-packages out. The packaged zip is trusted.
import truststore

truststore.inject_into_ssl()
runpy.run_module("factor_bridge", run_name="__main__", alter_sys=False)
