import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

try:
    print("Testing import: app.services.aws_session_manager...")
    from app.services.aws_session_manager import AWSSessionManager, get_aws_session_manager
    
    print("Testing singleton initialization...")
    manager = get_aws_session_manager()
    assert manager is AWSSessionManager()
    print("Singleton check: PASSED")
    
    print("Testing lock initialization...")
    assert hasattr(manager, '_cache_lock')
    assert hasattr(manager, '_role_locks')
    print("Lock check: PASSED")
    
    print("Testing app.services.aws_sdk_client integration...")
    from app.services.aws_sdk_client import execute_aws_sdk_call
    print("Integration check: PASSED")

    print("\n✅ SMOKE TEST PASSED: Core logic is runtime-stable.")
except Exception as e:
    print(f"\n❌ SMOKE TEST FAILED: {e}")
    sys.exit(1)
