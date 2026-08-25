def predict(score=0.5):
    """One number in, one verdict out."""
    score = float(score)
    return {"score": score, "risky": score > 0.7}
