import os
import xmlrpc.client
from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/api/odoo/add-staff")
async def add_staff(request: Request):
    data = await request.json()

    url = "https://odoo-custom-production.up.railway.app"
    db = "odoo_database"
    username = "odoo_user"
    password = "shuwafF2016"

    # Authenticate
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, password, {})

    # Interact with Odoo models
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    staff_id = models.execute_kw(
        db, uid, password,
        'hr.employee', 'create', [data]
    )

    return {"message": "Staff created", "staff_id": staff_id}
