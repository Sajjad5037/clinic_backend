async def websocket_endpoint(websocket: WebSocket):
    logger.info("New WebSocket connection attempt...")
    
    # Connect the client
    await manager.connect(websocket)
    logger.info(f"Client connected: {websocket.client}")

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {
                "patients": state.patients,
                "currentPatient": state.current_patient,
                "averageInspectionTime": state.get_average_time()
            }
        }
        await websocket.send_text(json.dumps(initial_state))
        logger.info(f"Sent initial state to client {websocket.client}: {initial_state}")

        while True:
            # Listen for messages from the client
            data = await websocket.receive_text()
            message = json.loads(data)
            logger.info(f"Received message from client {websocket.client}: {message}")

            # Handle different message types
            if message["type"] == "add_patient":
                state.add_patient(message["patient"])
                update_message = {
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time()
                    }
                }
                await manager.broadcast(update_message)
                logger.info(f"Broadcasted update_state: {update_message}")

            elif message["type"] == "mark_done":
                state.mark_as_done()
                update_message = {
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time()
                    }
                }
                await manager.broadcast(update_message)
                logger.info(f"Broadcasted update_state after mark_done: {update_message}")

            elif message["type"] == "reset_averageInspectionTime":
                state.reset_averageInspectionTime()
                reset_message = {
                    "type": "reset_averageInspectionTime",
                    "data": {                        
                        "averageInspectionTime": state.average_inspection_time
                    }
                }
                await manager.broadcast(reset_message)
                logger.info(f"Broadcasted reset_averageInspectionTime: {reset_message}")

    except WebSocketDisconnect:
        # Handle client disconnection
        manager.disconnect(websocket)
        logger.info(f"Client disconnected: {websocket.client}")

    except Exception as e:
        logger.error(f"Unexpected WebSocket error: {e}")
