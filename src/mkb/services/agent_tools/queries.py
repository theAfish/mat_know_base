from mkb.db.models import KnowledgeFrame, Projection, Space


def projection_context(session, projection_id):
    projection = session.query(Projection).filter_by(projection_id=projection_id).first()
    if not projection:
        return None, None, None
    frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
    space = session.query(Space).filter_by(space_id=projection.space_id).first()
    return projection, frame, space
