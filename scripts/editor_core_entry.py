import sys
from mmtr_editor.process_lifetime import watch_frozen_parent

watch_frozen_parent()

if sys.argv[1:]==['--dxc-worker']:
    from mmtr_editor.dxc_worker import main
elif sys.argv[1:]==['--material-worker']:
    from mmtr_editor.material_worker import main
elif sys.argv[1:]==['--mcp']:
    from mmtr_editor.mcp_server import main
else:
    from mmtr_editor.rpc import main

main()
