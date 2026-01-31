import vtk


def create_red_cube(center=(0.0, 0.0, 0.0),size=1.0,opacity=1.0):
    cube = vtk.vtkCubeSource()
    cube.SetCenter(*center)
    cube.SetXLength(size)
    cube.SetYLength(size)
    cube.SetZLength(size)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(1.0, 0.0, 0.0)  # red
    actor.GetProperty().SetOpacity(opacity)  # opaque

    return actor
