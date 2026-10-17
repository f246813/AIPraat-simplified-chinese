# Run from the repository root with Rscript.
source("ai/third_party/normalization-reference/phonR.R")
x <- as.matrix(read.csv("test-records/pronunciation/normalization-actual-r/input.csv", header=FALSE))
options(digits=17)
z <- normLobanov(x, group=rep(c("reference", "learner"), each=15))
write.table(z, file="test-records/pronunciation/normalization-actual-r/r-output.csv", sep=",", row.names=FALSE, col.names=FALSE)
cat("Executed pinned phonR normLobanov; rows=", nrow(z), "; columns=", ncol(z), "\n", sep="")
print(R.version.string)
sessionInfo()
