library(reticulate)

# Import lamindb
ln <- import("lamindb")

ln$track("EPnfDtJz8qbE0000", path="run-track.R")   # <-- unique id for the script, script path

# Create a sample R dataframe
r_df <- data.frame(
  id = 1:5,
  value = c(10.5, 20.3, 15.7, 25.1, 30.2),
  category = c("A", "B", "A", "C", "B")
)

# Save the dataframe as RDS
storage_path <- "example_data.rds"
saveRDS(r_df, storage_path)

ln$Artifact(storage_path, description="Example dataframe")$save()  # save an artifact
ln$finish()  # mark the script run as finished
