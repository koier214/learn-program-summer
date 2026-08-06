
# ============================================================
# ISF Level 3 - two-stage large-scale workflow
#
# Designed for:
#   - 1000+ mzXML files
#   - 100,000+ aligned features
#   - limited RAM
#   - resumable execution with progress bars
#
# Scientific workflow:
#   Stage 1:
#     1. Generate ALL candidates satisfying RT and mass-loss rules.
#     2. Do not keep only a fixed "top N".
#     3. Select up to several strong co-present files per pair.
#     4. Use these files for a lenient EIC-shape screen.
#
#   Stage 2:
#     1. For every pair that passes Stage 1, identify ALL files in
#        which precursor and fragment are both present.
#     2. Recalculate EIC correlations in all of those files.
#     3. Apply the final peakCOR threshold to the full-file result.
#
# Memory strategy:
#   - mzXML files are opened one at a time per worker.
#   - Feature intensities are stored in a file-backed float matrix.
#   - Candidate features are processed in batches without top-N truncation.
#   - Stage-2 pair-file assignments are stored in a file-backed integer matrix.
#   - Every expensive stage has checkpoints and progress bars.
#
# Required packages:
#   if (!requireNamespace("BiocManager", quietly = TRUE)) {
#     install.packages("BiocManager")
#   }
#   BiocManager::install("xcms")
#   install.packages(c("data.table", "bigmemory", "pbapply"))
# ============================================================


# ------------------------------------------------------------
# Package checks
# ------------------------------------------------------------
.isf_check_packages <- function() {
  required <- c(
    "xcms",
    "data.table",
    "bigmemory",
    "pbapply"
  )

  missing <- required[
    !vapply(
      required,
      requireNamespace,
      logical(1),
      quietly = TRUE
    )
  ]

  if (length(missing)) {
    stop(
      "Missing packages: ",
      paste(missing, collapse = ", "),
      "\n\nInstall them with:\n",
      'if (!requireNamespace("BiocManager", quietly = TRUE)) ',
      'install.packages("BiocManager")\n',
      'BiocManager::install("xcms")\n',
      'install.packages(c("data.table", "bigmemory", "pbapply"))'
    )
  }
}


# ------------------------------------------------------------
# General helpers
# ------------------------------------------------------------
.isf_make_dir <- function(path) {
  dir.create(
    path,
    recursive = TRUE,
    showWarnings = FALSE
  )

  normalizePath(
    path,
    winslash = "/",
    mustWork = TRUE
  )
}


.isf_clear_dir <- function(path) {
  if (!dir.exists(path)) {
    return(invisible(FALSE))
  }

  files <- list.files(
    path,
    full.names = TRUE,
    all.files = TRUE,
    no.. = TRUE
  )

  if (length(files)) {
    unlink(
      files,
      recursive = TRUE,
      force = TRUE
    )
  }

  invisible(TRUE)
}


.isf_atomic_save_rds <- function(
    object,
    path,
    compress = FALSE
) {
  temporary <- paste0(
    path,
    ".tmp_",
    Sys.getpid()
  )

  saveRDS(
    object,
    temporary,
    compress = compress
  )

  if (file.exists(path)) {
    unlink(path, force = TRUE)
  }

  moved <- file.rename(
    temporary,
    path
  )

  if (!moved) {
    unlink(temporary, force = TRUE)
    stop("Could not write checkpoint: ", path)
  }

  invisible(path)
}


.isf_hash_text <- function(lines) {
  temporary <- tempfile(
    pattern = "isf_signature_",
    fileext = ".txt"
  )

  writeLines(
    lines,
    temporary,
    useBytes = TRUE
  )

  value <- unname(
    tools::md5sum(temporary)
  )

  unlink(temporary)

  value
}


.isf_data_signature <- function(
    files,
    featureTable,
    mz,
    rt,
    intensity_cols
) {
  file_information <- file.info(files)

  lines <- c(
    paste0(
      "n_features=",
      nrow(featureTable)
    ),
    paste0(
      "n_files=",
      length(files)
    ),
    paste0(
      "file_names=",
      paste(
        basename(files),
        collapse = "|"
      )
    ),
    paste0(
      "file_sizes=",
      paste(
        file_information$size,
        collapse = "|"
      )
    ),
    paste0(
      "file_mtime=",
      paste(
        as.numeric(file_information$mtime),
        collapse = "|"
      )
    ),
    paste0(
      "mz_sum=",
      signif(
        sum(mz, na.rm = TRUE),
        15
      )
    ),
    paste0(
      "rt_sum=",
      signif(
        sum(rt, na.rm = TRUE),
        15
      )
    ),
    paste0(
      "intensity_columns=",
      paste(
        names(featureTable)[intensity_cols],
        collapse = "|"
      )
    )
  )

  .isf_hash_text(lines)
}


.isf_run_signature <- function(
    data_signature,
    run_id,
    peakCOR,
    screenCOR,
    loss,
    mz.tol,
    rt.tol,
    candidate.rt,
    min_copresent_files,
    stage1_files_per_pair,
    stage1_min_valid,
    stage1_fail_open_sparse,
    prefilter_cor,
    prefilter_samples,
    min_final_valid,
    final_min_proportion,
    candidate_feature_chunk,
    candidate_batch_size,
    stage2_pair_batch_size,
    block.width,
    smooth_level
) {
  prefilter_text <- if (is.null(prefilter_cor)) {
    "NULL"
  } else {
    as.character(prefilter_cor)
  }

  screen_text <- if (is.null(screenCOR)) {
    "NULL"
  } else {
    as.character(screenCOR)
  }

  lines <- c(
    paste0("data_signature=", data_signature),
    paste0("run_id=", run_id),
    paste0("peakCOR=", peakCOR),
    paste0("screenCOR=", screen_text),
    paste0("loss=", loss),
    paste0("mz.tol=", mz.tol),
    paste0("rt.tol=", rt.tol),
    paste0("candidate.rt=", candidate.rt),
    paste0(
      "min_copresent_files=",
      min_copresent_files
    ),
    paste0(
      "stage1_files_per_pair=",
      stage1_files_per_pair
    ),
    paste0(
      "stage1_min_valid=",
      stage1_min_valid
    ),
    paste0(
      "stage1_fail_open_sparse=",
      stage1_fail_open_sparse
    ),
    paste0(
      "prefilter_cor=",
      prefilter_text
    ),
    paste0(
      "prefilter_samples=",
      prefilter_samples
    ),
    paste0(
      "min_final_valid=",
      min_final_valid
    ),
    paste0(
      "final_min_proportion=",
      final_min_proportion
    ),
    paste0(
      "candidate_feature_chunk=",
      candidate_feature_chunk
    ),
    paste0(
      "candidate_batch_size=",
      candidate_batch_size
    ),
    paste0(
      "stage2_pair_batch_size=",
      stage2_pair_batch_size
    ),
    paste0(
      "block.width=",
      block.width
    ),
    paste0(
      "smooth_level=",
      smooth_level
    )
  )

  .isf_hash_text(lines)
}


.isf_bm_matrix <- function(
    bm,
    rows,
    cols = NULL
) {
  rows <- as.integer(rows)

  if (!length(rows)) {
    if (is.null(cols)) {
      return(
        matrix(
          numeric(),
          nrow = 0L,
          ncol = ncol(bm)
        )
      )
    }

    return(
      matrix(
        numeric(),
        nrow = 0L,
        ncol = length(cols)
      )
    )
  }

  if (is.null(cols)) {
    values <- bm[rows, ]
    n_columns <- ncol(bm)
  } else {
    cols <- as.integer(cols)
    values <- bm[rows, cols]
    n_columns <- length(cols)
  }

  matrix(
    as.numeric(values),
    nrow = length(rows),
    ncol = n_columns
  )
}


.isf_split_indices <- function(
    n,
    chunk_size
) {
  if (n <= 0L) {
    return(list())
  }

  ids <- seq_len(n)

  split(
    ids,
    ceiling(ids / chunk_size)
  )
}


.isf_empty_hits <- function() {
  data.table::data.table(
    pair_id = integer(),
    precursor = integer(),
    fragment = integer(),
    n_copresent = integer(),
    prefilter_cor = numeric(),
    stage1_valid_files = integer(),
    stage1_mean_cor = numeric(),
    stage1_max_cor = numeric(),
    stage1_prop_ge_peak = numeric(),
    stage1_pass = logical(),
    stage2_id = integer(),
    final_valid_files = integer(),
    final_mean_cor = numeric(),
    final_prop_ge_peak = numeric(),
    final_min_cor = numeric(),
    final_max_cor = numeric(),
    final_pass = logical()
  )
}


# ------------------------------------------------------------
# Signal processing helpers
# ------------------------------------------------------------
.isf_peak_smooth <- function(
    x,
    level = 2L
) {
  x <- as.numeric(x)
  x[!is.finite(x)] <- 0

  n <- as.integer(level)
  N <- length(x)

  if (
    N <= 2L * n ||
    N < 3L ||
    max(x) == min(x)
  ) {
    return(x)
  }

  y <- numeric(N)

  for (i in seq_len(n)) {
    weights <- c(
      (n - i + 2L):(n + 1L),
      n:1L
    )

    values <- x[
      seq_len(i + n)
    ]

    y[i] <- sum(
      weights * values
    ) / sum(weights)
  }

  middle <- (n + 1L):(N - n)

  for (i in middle) {
    weights <- c(
      1L:(n + 1L),
      n:1L
    )

    values <- x[
      (i - n):(i + n)
    ]

    y[i] <- sum(
      weights * values
    ) / sum(weights)
  }

  for (i in (N - n + 1L):N) {
    weights <- c(
      1L:n,
      (n + 1L):(n + i - N + 1L)
    )

    values <- x[
      (i - n):N
    ]

    y[i] <- sum(
      weights * values
    ) / sum(weights)
  }

  y
}


.isf_cor_one_to_many <- function(
    precursor_eic,
    fragment_matrix
) {
  x <- as.numeric(
    precursor_eic
  )

  Y <- as.matrix(
    fragment_matrix
  )

  if (
    !length(x) ||
    !ncol(Y) ||
    nrow(Y) != length(x)
  ) {
    return(
      rep(
        NA_real_,
        ncol(Y)
      )
    )
  }

  x[!is.finite(x)] <- 0
  Y[!is.finite(Y)] <- 0

  x_centered <- x - mean(x)

  Y_centered <- sweep(
    Y,
    2L,
    colMeans(Y),
    FUN = "-"
  )

  denominator <- sqrt(
    sum(x_centered * x_centered) *
      colSums(
        Y_centered * Y_centered
      )
  )

  numerator <- drop(
    crossprod(
      x_centered,
      Y_centered
    )
  )

  result <- numerator / denominator

  result[
    !is.finite(result) |
      denominator == 0
  ] <- NA_real_

  result
}


.isf_row_cor <- function(
    candidate_matrix,
    precursor_vector
) {
  Y <- as.matrix(
    candidate_matrix
  )

  x <- as.numeric(
    precursor_vector
  )

  if (
    !nrow(Y) ||
    ncol(Y) != length(x)
  ) {
    return(numeric())
  }

  x[!is.finite(x)] <- 0
  Y[!is.finite(Y)] <- 0

  x <- log1p(
    pmax(x, 0)
  )

  Y <- log1p(
    pmax(Y, 0)
  )

  x_centered <- x - mean(x)

  Y_centered <- Y - rowMeans(Y)

  denominator <- sqrt(
    rowSums(
      Y_centered * Y_centered
    ) *
      sum(
        x_centered * x_centered
      )
  )

  numerator <- drop(
    Y_centered %*% x_centered
  )

  result <- numerator / denominator

  result[
    !is.finite(result) |
      denominator == 0
  ] <- NA_real_

  result
}


# ------------------------------------------------------------
# File-backed intensity matrix
# ------------------------------------------------------------
.isf_prepare_intensity_matrix <- function(
    featureTable,
    intensity_cols,
    cache_dir,
    rebuild = FALSE
) {
  cache_dir <- .isf_make_dir(
    cache_dir
  )

  backing_name <- "intensity_float.bin"

  descriptor_path <- file.path(
    cache_dir,
    "intensity_float.desc"
  )

  metadata_path <- file.path(
    cache_dir,
    "intensity_metadata.rds"
  )

  expected_metadata <- list(
    n_features = nrow(featureTable),
    n_files = length(intensity_cols),
    intensity_names = names(featureTable)[
      intensity_cols
    ]
  )

  reusable <- (
    !rebuild &&
      file.exists(descriptor_path) &&
      file.exists(metadata_path)
  )

  if (reusable) {
    stored_metadata <- readRDS(
      metadata_path
    )

    reusable <- identical(
      stored_metadata,
      expected_metadata
    )
  }

  if (reusable) {
    message(
      "Reusing the file-backed intensity matrix."
    )

    return(
      bigmemory::attach.big.matrix(
        descriptor_path
      )
    )
  }

  unlink(
    file.path(
      cache_dir,
      backing_name
    ),
    force = TRUE
  )

  unlink(
    descriptor_path,
    force = TRUE
  )

  unlink(
    metadata_path,
    force = TRUE
  )

  message(
    "Creating the file-backed float intensity matrix..."
  )

  bm <- bigmemory::filebacked.big.matrix(
    nrow = nrow(featureTable),
    ncol = length(intensity_cols),
    type = "float",
    backingfile = backing_name,
    backingpath = cache_dir,
    descriptorfile = basename(
      descriptor_path
    ),
    dimnames = list(
      NULL,
      names(featureTable)[
        intensity_cols
      ]
    )
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = length(intensity_cols),
    style = 3
  )

  for (j in seq_along(intensity_cols)) {
    values <- suppressWarnings(
      as.numeric(
        featureTable[
          [intensity_cols[j]]
        ]
      )
    )

    values[
      !is.finite(values)
    ] <- 0

    values[
      values < 0
    ] <- 0

    bm[, j] <- values

    utils::setTxtProgressBar(
      progress,
      j
    )
  }

  close(progress)

  .isf_atomic_save_rds(
    expected_metadata,
    metadata_path
  )

  bm
}


# ------------------------------------------------------------
# Candidate generation
# ------------------------------------------------------------
.isf_build_candidate_chunk <- function(
    precursor_ids,
    bm,
    mz,
    rt,
    order_rt,
    position_in_order,
    rt_sorted,
    mz_sorted,
    candidate.rt,
    loss,
    min_copresent_files,
    stage1_files_per_pair,
    candidate_batch_size,
    prefilter_cor,
    prefilter_file_ids
) {
  pair_parts <- list()
  assignment_parts <- list()

  pair_part_counter <- 0L
  assignment_part_counter <- 0L
  next_local_pair_id <- 1L

  n_files <- ncol(bm)

  for (precursor_id in precursor_ids) {
    precursor_position <- position_in_order[
      precursor_id
    ]

    lower_rt <- rt[
      precursor_id
    ] - candidate.rt

    upper_rt <- rt[
      precursor_id
    ] + candidate.rt

    left <- findInterval(
      lower_rt,
      rt_sorted
    ) + 1L

    right <- findInterval(
      upper_rt,
      rt_sorted
    )

    if (
      left > right ||
      left > length(rt_sorted) ||
      right < 1L
    ) {
      next
    }

    candidate_positions <- left:right

    candidate_positions <- candidate_positions[
      rt_sorted[
        candidate_positions
      ] > lower_rt &
        rt_sorted[
          candidate_positions
        ] < upper_rt &
        mz_sorted[
          candidate_positions
        ] <=
          mz[
            precursor_id
          ] - loss
    ]

    if (!length(candidate_positions)) {
      next
    }

    candidate_ids <- order_rt[
      candidate_positions
    ]

    precursor_all <- as.numeric(
      bm[
        precursor_id,
      ]
    )

    precursor_all[
      !is.finite(precursor_all)
    ] <- 0

    precursor_positive <- (
      precursor_all > 0
    )

    if (
      sum(precursor_positive) <
        min_copresent_files
    ) {
      next
    }

    candidate_batches <- .isf_split_indices(
      length(candidate_ids),
      candidate_batch_size
    )

    for (batch_rows in candidate_batches) {
      current_candidate_ids <- candidate_ids[
        batch_rows
      ]

      fragment_all <- .isf_bm_matrix(
        bm,
        rows = current_candidate_ids
      )

      fragment_all[
        !is.finite(fragment_all)
      ] <- 0

      common_presence <- (
        fragment_all > 0
      )

      common_presence[
        ,
        !precursor_positive
      ] <- FALSE

      copresent_count <- rowSums(
        common_presence
      )

      eligible <- which(
        copresent_count >=
          min_copresent_files
      )

      if (!length(eligible)) {
        next
      }

      current_candidate_ids <-
        current_candidate_ids[
          eligible
        ]

      fragment_all <- fragment_all[
        eligible,
        ,
        drop = FALSE
      ]

      common_presence <-
        common_presence[
          eligible,
          ,
          drop = FALSE
        ]

      copresent_count <-
        copresent_count[
          eligible
        ]

      sample_cor <- rep(
        NA_real_,
        length(
          current_candidate_ids
        )
      )

      if (!is.null(prefilter_cor)) {
        sample_cor <- .isf_row_cor(
          fragment_all[
            ,
            prefilter_file_ids,
            drop = FALSE
          ],
          precursor_all[
            prefilter_file_ids
          ]
        )

        keep_prefilter <- which(
          is.finite(sample_cor) &
            sample_cor >=
              prefilter_cor
        )

        if (!length(keep_prefilter)) {
          next
        }

        current_candidate_ids <-
          current_candidate_ids[
            keep_prefilter
          ]

        fragment_all <- fragment_all[
          keep_prefilter,
          ,
          drop = FALSE
        ]

        common_presence <-
          common_presence[
            keep_prefilter,
            ,
            drop = FALSE
          ]

        copresent_count <-
          copresent_count[
            keep_prefilter
          ]

        sample_cor <- sample_cor[
          keep_prefilter
        ]
      }

      number_candidates <- length(
        current_candidate_ids
      )

      local_pair_ids <- seq.int(
        from = next_local_pair_id,
        length.out = number_candidates
      )

      next_local_pair_id <-
        next_local_pair_id +
          number_candidates

      pair_part_counter <-
        pair_part_counter + 1L

      pair_parts[
        [pair_part_counter]
      ] <- data.table::data.table(
        pair_id = as.integer(
          local_pair_ids
        ),
        precursor = as.integer(
          rep.int(
            precursor_id,
            number_candidates
          )
        ),
        fragment = as.integer(
          current_candidate_ids
        ),
        n_copresent = as.integer(
          copresent_count
        ),
        prefilter_cor = as.numeric(
          sample_cor
        )
      )

      assignment_rows <- vector(
        "list",
        number_candidates
      )

      for (j in seq_len(number_candidates)) {
        common_files <- which(
          common_presence[j, ]
        )

        joint_score <- sqrt(
          pmax(
            precursor_all[
              common_files
            ],
            0
          ) *
            pmax(
              fragment_all[
                j,
                common_files
              ],
              0
            )
        )

        representative_order <- order(
          joint_score,
          decreasing = TRUE
        )

        representative_files <- common_files[
          head(
            representative_order,
            stage1_files_per_pair
          )
        ]

        assignment_rows[[j]] <-
          data.table::data.table(
            file_id = as.integer(
              representative_files
            ),
            pair_id = as.integer(
              rep.int(
                local_pair_ids[j],
                length(
                  representative_files
                )
              )
            ),
            precursor = as.integer(
              rep.int(
                precursor_id,
                length(
                  representative_files
                )
              )
            ),
            fragment = as.integer(
              rep.int(
                current_candidate_ids[j],
                length(
                  representative_files
                )
              )
            )
          )
      }

      assignment_part_counter <-
        assignment_part_counter + 1L

      assignment_parts[
        [assignment_part_counter]
      ] <- data.table::rbindlist(
        assignment_rows,
        use.names = TRUE
      )
    }
  }

  pairs <- data.table::rbindlist(
    pair_parts,
    use.names = TRUE,
    fill = TRUE
  )

  assignments <- data.table::rbindlist(
    assignment_parts,
    use.names = TRUE,
    fill = TRUE
  )

  if (!nrow(pairs)) {
    pairs <- data.table::data.table(
      pair_id = integer(),
      precursor = integer(),
      fragment = integer(),
      n_copresent = integer(),
      prefilter_cor = numeric()
    )
  }

  if (!nrow(assignments)) {
    assignments <- data.table::data.table(
      file_id = integer(),
      pair_id = integer(),
      precursor = integer(),
      fragment = integer()
    )
  }

  list(
    pairs = pairs,
    assignments = assignments
  )
}


.isf_prepare_candidates <- function(
    bm,
    mz,
    rt,
    candidate.rt,
    loss,
    min_copresent_files,
    stage1_files_per_pair,
    candidate_feature_chunk,
    candidate_batch_size,
    prefilter_cor,
    prefilter_file_ids,
    candidate_chunk_dir,
    combined_pairs_path,
    combined_assignments_path,
    rebuild = FALSE
) {
  if (
    !rebuild &&
      file.exists(combined_pairs_path) &&
      file.exists(combined_assignments_path)
  ) {
    message(
      "Reusing combined candidate checkpoints."
    )

    return(
      list(
        pairs = readRDS(
          combined_pairs_path
        ),
        assignments = readRDS(
          combined_assignments_path
        )
      )
    )
  }

  if (rebuild) {
    .isf_clear_dir(
      candidate_chunk_dir
    )
  }

  n_features <- length(mz)

  order_rt <- order(rt)

  rt_sorted <- rt[
    order_rt
  ]

  mz_sorted <- mz[
    order_rt
  ]

  position_in_order <- integer(
    n_features
  )

  position_in_order[
    order_rt
  ] <- seq_len(n_features)

  feature_chunks <- .isf_split_indices(
    n_features,
    candidate_feature_chunk
  )

  message(
    "Generating candidate chunks without a top-N truncation..."
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = length(feature_chunks),
    style = 3
  )

  for (chunk_id in seq_along(feature_chunks)) {
    checkpoint_path <- file.path(
      candidate_chunk_dir,
      sprintf(
        "candidate_chunk_%06d.rds",
        chunk_id
      )
    )

    if (!file.exists(checkpoint_path)) {
      current <- .isf_build_candidate_chunk(
        precursor_ids =
          feature_chunks[[chunk_id]],
        bm = bm,
        mz = mz,
        rt = rt,
        order_rt = order_rt,
        position_in_order =
          position_in_order,
        rt_sorted = rt_sorted,
        mz_sorted = mz_sorted,
        candidate.rt = candidate.rt,
        loss = loss,
        min_copresent_files =
          min_copresent_files,
        stage1_files_per_pair =
          stage1_files_per_pair,
        candidate_batch_size =
          candidate_batch_size,
        prefilter_cor = prefilter_cor,
        prefilter_file_ids =
          prefilter_file_ids
      )

      .isf_atomic_save_rds(
        current,
        checkpoint_path
      )

      rm(current)
      gc(FALSE)
    }

    utils::setTxtProgressBar(
      progress,
      chunk_id
    )
  }

  close(progress)

  message(
    "Combining candidate chunks..."
  )

  pair_parts <- vector(
    "list",
    length(feature_chunks)
  )

  assignment_parts <- vector(
    "list",
    length(feature_chunks)
  )

  next_global_pair_id <- 1L

  progress <- utils::txtProgressBar(
    min = 0,
    max = length(feature_chunks),
    style = 3
  )

  for (chunk_id in seq_along(feature_chunks)) {
    checkpoint_path <- file.path(
      candidate_chunk_dir,
      sprintf(
        "candidate_chunk_%06d.rds",
        chunk_id
      )
    )

    current <- readRDS(
      checkpoint_path
    )

    if (nrow(current$pairs)) {
      offset <- (
        next_global_pair_id - 1L
      )

      current$pairs[
        ,
        pair_id := pair_id + offset
      ]

      current$assignments[
        ,
        pair_id := pair_id + offset
      ]

      next_global_pair_id <-
        next_global_pair_id +
          nrow(current$pairs)

      pair_parts[[chunk_id]] <-
        current$pairs

      assignment_parts[[chunk_id]] <-
        current$assignments
    }

    rm(current)

    utils::setTxtProgressBar(
      progress,
      chunk_id
    )
  }

  close(progress)

  pairs <- data.table::rbindlist(
    pair_parts,
    use.names = TRUE,
    fill = TRUE
  )

  assignments <- data.table::rbindlist(
    assignment_parts,
    use.names = TRUE,
    fill = TRUE
  )

  rm(
    pair_parts,
    assignment_parts
  )

  gc(FALSE)

  if (!nrow(pairs)) {
    pairs <- data.table::data.table(
      pair_id = integer(),
      precursor = integer(),
      fragment = integer(),
      n_copresent = integer(),
      prefilter_cor = numeric()
    )
  }

  if (!nrow(assignments)) {
    assignments <- data.table::data.table(
      file_id = integer(),
      pair_id = integer(),
      precursor = integer(),
      fragment = integer()
    )
  }

  .isf_atomic_save_rds(
    pairs,
    combined_pairs_path
  )

  .isf_atomic_save_rds(
    assignments,
    combined_assignments_path
  )

  list(
    pairs = pairs,
    assignments = assignments
  )
}


# ------------------------------------------------------------
# Write compact per-file assignment checkpoints
# ------------------------------------------------------------
.isf_write_assignments_by_file <- function(
    assignments,
    n_files,
    assignment_dir,
    complete_flag,
    rebuild = FALSE
) {
  if (
    !rebuild &&
      file.exists(complete_flag)
  ) {
    message(
      "Reusing per-file Stage-1 assignment checkpoints."
    )

    return(invisible(TRUE))
  }

  .isf_clear_dir(
    assignment_dir
  )

  if (nrow(assignments)) {
    data.table::setkey(
      assignments,
      file_id
    )
  }

  progress <- utils::txtProgressBar(
    min = 0,
    max = n_files,
    style = 3
  )

  for (file_id in seq_len(n_files)) {
    assignment_path <- file.path(
      assignment_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    if (nrow(assignments)) {
      current <- assignments[
        data.table::J(file_id),
        .(
          pair_id,
          precursor,
          fragment
        )
      ]
    } else {
      current <- data.table::data.table(
        pair_id = integer(),
        precursor = integer(),
        fragment = integer()
      )
    }

    .isf_atomic_save_rds(
      current,
      assignment_path
    )

    utils::setTxtProgressBar(
      progress,
      file_id
    )
  }

  close(progress)

  writeLines(
    "complete",
    complete_flag
  )

  invisible(TRUE)
}


# ------------------------------------------------------------
# Generic EIC computation for one file
# ------------------------------------------------------------
.isf_process_file_assignments <- function(
    file_id,
    assignments,
    mzxml_file,
    mz,
    rt,
    mz.tol,
    rt.tol,
    block.width,
    smooth_level,
    result_path
) {
  if (file.exists(result_path)) {
    return(
      list(
        file_id = file_id,
        skipped = TRUE,
        n_correlations = NA_integer_
      )
    )
  }

  if (!nrow(assignments)) {
    empty_result <- data.table::data.table(
      pair_id = integer(),
      cor = numeric()
    )

    .isf_atomic_save_rds(
      empty_result,
      result_path
    )

    return(
      list(
        file_id = file_id,
        skipped = FALSE,
        n_correlations = 0L
      )
    )
  }

  assignments <- data.table::as.data.table(
    assignments
  )

  assignments[
    ,
    block := floor(
      rt[precursor] /
        block.width
    )
  ]

  data.table::setorder(
    assignments,
    block,
    precursor,
    fragment
  )

  xraw <- xcms::xcmsRaw(
    mzxml_file,
    profstep = 0
  )

  on.exit(
    {
      rm(xraw)
      gc(FALSE)
    },
    add = TRUE
  )

  first_rt <- xraw@scantime[1L]

  last_rt <- tail(
    xraw@scantime,
    1L
  )

  block_ids <- unique(
    assignments$block
  )

  result_parts <- vector(
    "list",
    length(block_ids)
  )

  result_part_counter <- 0L

  for (current_block_id in block_ids) {
    current_block <- assignments[
      block == current_block_id
    ]

    precursor_ids <- unique(
      current_block$precursor
    )

    block_rt_lower <- max(
      first_rt,
      min(
        rt[
          precursor_ids
        ]
      ) - rt.tol
    )

    block_rt_upper <- min(
      last_rt,
      max(
        rt[
          precursor_ids
        ]
      ) + rt.tol
    )

    if (
      !is.finite(block_rt_lower) ||
      !is.finite(block_rt_upper) ||
      block_rt_upper <= block_rt_lower
    ) {
      next
    }

    required_features <- unique(
      c(
        current_block$precursor,
        current_block$fragment
      )
    )

    valid_mz <- (
      mz[
        required_features
      ] - mz.tol >=
        xraw@mzrange[1L]
    ) & (
      mz[
        required_features
      ] + mz.tol <=
        xraw@mzrange[2L]
    )

    required_features <-
      required_features[
        valid_mz
      ]

    if (!length(required_features)) {
      next
    }

    eic_list <- vector(
      "list",
      length(required_features)
    )

    valid_feature <- logical(
      length(required_features)
    )

    for (j in seq_along(required_features)) {
      feature_id <- required_features[j]

      current_eic <- tryCatch(
        xcms::rawEIC(
          xraw,
          mzrange = c(
            mz[feature_id] - mz.tol,
            mz[feature_id] + mz.tol
          ),
          rtrange = c(
            block_rt_lower,
            block_rt_upper
          )
        ),
        error = function(e) {
          NULL
        }
      )

      if (
        is.null(current_eic) ||
        !length(current_eic$scan) ||
        !length(current_eic$intensity)
      ) {
        next
      }

      eic_list[[j]] <- current_eic
      valid_feature[j] <- TRUE
    }

    if (!any(valid_feature)) {
      next
    }

    required_features <-
      required_features[
        valid_feature
      ]

    eic_list <- eic_list[
      valid_feature
    ]

    all_scans <- sort(
      unique(
        unlist(
          lapply(
            eic_list,
            function(z) {
              z$scan
            }
          ),
          use.names = FALSE
        )
      )
    )

    if (length(all_scans) < 5L) {
      next
    }

    eic_matrix <- matrix(
      0,
      nrow = length(all_scans),
      ncol = length(required_features),
      dimnames = list(
        NULL,
        as.character(
          required_features
        )
      )
    )

    for (j in seq_along(eic_list)) {
      scan_position <- match(
        eic_list[[j]]$scan,
        all_scans
      )

      eic_matrix[
        scan_position,
        j
      ] <- eic_list[
        [j]
      ]$intensity
    }

    for (j in seq_len(ncol(eic_matrix))) {
      eic_matrix[, j] <-
        .isf_peak_smooth(
          eic_matrix[, j],
          level = smooth_level
        )
    }

    scan_times <- xraw@scantime[
      all_scans
    ]

    precursor_groups <- split(
      seq_len(
        nrow(current_block)
      ),
      current_block$precursor
    )

    block_result_parts <- vector(
      "list",
      length(precursor_groups)
    )

    block_result_counter <- 0L

    for (group_rows in precursor_groups) {
      precursor_id <- current_block$precursor[
        group_rows[1L]
      ]

      precursor_name <- as.character(
        precursor_id
      )

      if (
        !precursor_name %in%
          colnames(eic_matrix)
      ) {
        next
      }

      window_index <- which(
        scan_times >=
          rt[precursor_id] -
            rt.tol &
          scan_times <=
            rt[precursor_id] +
              rt.tol
      )

      if (length(window_index) < 5L) {
        next
      }

      fragment_ids <- current_block$fragment[
        group_rows
      ]

      fragment_names <- as.character(
        fragment_ids
      )

      valid_fragment <- (
        fragment_names %in%
          colnames(eic_matrix)
      )

      if (!any(valid_fragment)) {
        next
      }

      group_rows <- group_rows[
        valid_fragment
      ]

      fragment_names <- fragment_names[
        valid_fragment
      ]

      precursor_eic <- eic_matrix[
        window_index,
        precursor_name
      ]

      fragment_matrix <- eic_matrix[
        window_index,
        fragment_names,
        drop = FALSE
      ]

      correlations <- .isf_cor_one_to_many(
        precursor_eic,
        fragment_matrix
      )

      keep <- which(
        is.finite(correlations)
      )

      if (!length(keep)) {
        next
      }

      block_result_counter <-
        block_result_counter + 1L

      block_result_parts[
        [block_result_counter]
      ] <- data.table::data.table(
        pair_id = as.integer(
          current_block$pair_id[
            group_rows[
              keep
            ]
          ]
        ),
        cor = as.numeric(
          correlations[
            keep
          ]
        )
      )
    }

    if (block_result_counter) {
      result_part_counter <-
        result_part_counter + 1L

      result_parts[
        [result_part_counter]
      ] <- data.table::rbindlist(
        block_result_parts[
          seq_len(
            block_result_counter
          )
        ],
        use.names = TRUE
      )
    }

    rm(
      eic_list,
      eic_matrix
    )
  }

  if (result_part_counter) {
    result <- data.table::rbindlist(
      result_parts[
        seq_len(
          result_part_counter
        )
      ],
      use.names = TRUE,
      fill = TRUE
    )

    if (anyDuplicated(result$pair_id)) {
      result <- result[
        ,
        .(
          cor = mean(
            cor,
            na.rm = TRUE
          )
        ),
        by = pair_id
      ]
    }
  } else {
    result <- data.table::data.table(
      pair_id = integer(),
      cor = numeric()
    )
  }

  .isf_atomic_save_rds(
    result,
    result_path
  )

  list(
    file_id = file_id,
    skipped = FALSE,
    n_correlations = nrow(result)
  )
}


.isf_run_stage1_files <- function(
    file_ids,
    files,
    mz,
    rt,
    mz.tol,
    rt.tol,
    block.width,
    smooth_level,
    assignment_dir,
    result_dir,
    workers
) {
  worker <- function(file_id) {
    assignment_path <- file.path(
      assignment_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    result_path <- file.path(
      result_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    assignments <- readRDS(
      assignment_path
    )

    .isf_process_file_assignments(
      file_id = file_id,
      assignments = assignments,
      mzxml_file = files[file_id],
      mz = mz,
      rt = rt,
      mz.tol = mz.tol,
      rt.tol = rt.tol,
      block.width = block.width,
      smooth_level = smooth_level,
      result_path = result_path
    )
  }

  old_options <- pbapply::pboptions(
    type = if (interactive()) {
      "timer"
    } else {
      "txt"
    }
  )

  on.exit(
    pbapply::pboptions(
      old_options
    ),
    add = TRUE
  )

  if (workers <= 1L) {
    invisible(
      pbapply::pblapply(
        file_ids,
        worker
      )
    )

    return(invisible(TRUE))
  }

  cluster <- parallel::makePSOCKcluster(
    workers
  )

  on.exit(
    parallel::stopCluster(
      cluster
    ),
    add = TRUE
  )

  parallel::clusterEvalQ(
    cluster,
    {
      library(xcms)
      library(data.table)
      data.table::setDTthreads(1L)
      NULL
    }
  )

  parallel::clusterExport(
    cluster,
    varlist = c(
      ".isf_peak_smooth",
      ".isf_cor_one_to_many",
      ".isf_atomic_save_rds",
      ".isf_process_file_assignments"
    ),
    envir = environment()
  )

  invisible(
    pbapply::pblapply(
      file_ids,
      worker,
      cl = cluster
    )
  )

  invisible(TRUE)
}


# ------------------------------------------------------------
# Stage-1 aggregation
# ------------------------------------------------------------
.isf_aggregate_stage1 <- function(
    pairs,
    n_files,
    stage1_result_dir,
    peakCOR,
    screenCOR,
    stage1_min_valid,
    stage1_fail_open_sparse
) {
  n_pairs <- nrow(pairs)

  cor_sum <- numeric(
    n_pairs
  )

  cor_count <- integer(
    n_pairs
  )

  cor_max <- rep(
    -Inf,
    n_pairs
  )

  cor_ge_peak <- integer(
    n_pairs
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = n_files,
    style = 3
  )

  for (file_id in seq_len(n_files)) {
    result_path <- file.path(
      stage1_result_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    if (file.exists(result_path)) {
      current <- readRDS(
        result_path
      )

      if (nrow(current)) {
        valid <- (
          current$pair_id >= 1L &
            current$pair_id <=
              n_pairs &
            is.finite(
              current$cor
            )
        )

        if (any(valid)) {
          ids <- current$pair_id[
            valid
          ]

          values <- current$cor[
            valid
          ]

          cor_sum[ids] <-
            cor_sum[ids] +
              values

          cor_count[ids] <-
            cor_count[ids] +
              1L

          cor_max[ids] <- pmax(
            cor_max[ids],
            values
          )

          cor_ge_peak[ids] <-
            cor_ge_peak[ids] +
              as.integer(
                values >= peakCOR
              )
        }
      }

      rm(current)
    }

    utils::setTxtProgressBar(
      progress,
      file_id
    )
  }

  close(progress)

  pairs[
    ,
    stage1_valid_files :=
      cor_count[pair_id]
  ]

  pairs[
    ,
    stage1_mean_cor :=
      data.table::fifelse(
        stage1_valid_files > 0L,
        cor_sum[pair_id] /
          stage1_valid_files,
        NA_real_
      )
  ]

  pairs[
    ,
    stage1_max_cor :=
      data.table::fifelse(
        stage1_valid_files > 0L,
        cor_max[pair_id],
        NA_real_
      )
  ]

  pairs[
    ,
    stage1_prop_ge_peak :=
      data.table::fifelse(
        stage1_valid_files > 0L,
        cor_ge_peak[pair_id] /
          stage1_valid_files,
        NA_real_
      )
  ]

  enough_stage1 <- (
    pairs$stage1_valid_files >=
      stage1_min_valid
  )

  if (is.null(screenCOR)) {
    pass_regular <- enough_stage1
  } else {
    pass_regular <- (
      enough_stage1 &
        (
          pairs$stage1_mean_cor >=
            screenCOR |
            pairs$stage1_max_cor >=
              peakCOR
        )
    )
  }

  pass_sparse <- rep(
    FALSE,
    n_pairs
  )

  if (stage1_fail_open_sparse) {
    pass_sparse <- (
      pairs$n_copresent <
        stage1_min_valid &
        pairs$stage1_valid_files > 0L
    )
  }

  pairs[
    ,
    stage1_pass :=
      pass_regular |
        pass_sparse
  ]

  pairs
}


# ------------------------------------------------------------
# Stage-2 file-backed assignment matrix
# ------------------------------------------------------------
.isf_prepare_stage2_assignments <- function(
    passed_pairs,
    bm,
    n_files,
    cache_dir,
    pair_batch_size,
    rebuild = FALSE
) {
  cache_dir <- .isf_make_dir(
    cache_dir
  )

  descriptor_path <- file.path(
    cache_dir,
    "stage2_assignments.desc"
  )

  backing_name <- "stage2_assignments.bin"

  metadata_path <- file.path(
    cache_dir,
    "stage2_assignment_metadata.rds"
  )

  expected_pair_signature <- c(
    nrow(passed_pairs),
    sum(
      passed_pairs$pair_id,
      na.rm = TRUE
    ),
    sum(
      passed_pairs$precursor,
      na.rm = TRUE
    ),
    sum(
      passed_pairs$fragment,
      na.rm = TRUE
    )
  )

  reusable <- (
    !rebuild &&
      file.exists(descriptor_path) &&
      file.exists(metadata_path)
  )

  if (reusable) {
    metadata <- readRDS(
      metadata_path
    )

    reusable <- identical(
      metadata$pair_signature,
      expected_pair_signature
    )
  }

  if (reusable) {
    message(
      "Reusing the Stage-2 file-backed assignment matrix."
    )

    return(
      list(
        matrix = bigmemory::attach.big.matrix(
          descriptor_path
        ),
        counts = metadata$counts,
        starts = metadata$starts,
        descriptor_path = descriptor_path,
        total_assignments =
          metadata$total_assignments
      )
    )
  }

  unlink(
    file.path(
      cache_dir,
      backing_name
    ),
    force = TRUE
  )

  unlink(
    descriptor_path,
    force = TRUE
  )

  unlink(
    metadata_path,
    force = TRUE
  )

  if (!nrow(passed_pairs)) {
    metadata <- list(
      pair_signature =
        expected_pair_signature,
      counts = numeric(n_files),
      starts = numeric(n_files),
      total_assignments = 0
    )

    .isf_atomic_save_rds(
      metadata,
      metadata_path
    )

    return(
      list(
        matrix = NULL,
        counts = metadata$counts,
        starts = metadata$starts,
        descriptor_path = descriptor_path,
        total_assignments = 0
      )
    )
  }

  pair_batches <- .isf_split_indices(
    nrow(passed_pairs),
    pair_batch_size
  )

  message(
    "Stage 2 assignment pass 1/2: counting all co-present pair-file combinations..."
  )

  counts <- numeric(
    n_files
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = length(pair_batches),
    style = 3
  )

  for (batch_id in seq_along(pair_batches)) {
    rows <- pair_batches[
      [batch_id]
    ]

    precursor_matrix <- .isf_bm_matrix(
      bm,
      rows = passed_pairs$precursor[
        rows
      ]
    )

    fragment_matrix <- .isf_bm_matrix(
      bm,
      rows = passed_pairs$fragment[
        rows
      ]
    )

    common <- (
      precursor_matrix > 0 &
        fragment_matrix > 0
    )

    counts <- counts +
      colSums(common)

    rm(
      precursor_matrix,
      fragment_matrix,
      common
    )

    utils::setTxtProgressBar(
      progress,
      batch_id
    )
  }

  close(progress)

  total_assignments <- sum(
    counts
  )

  if (total_assignments <= 0) {
    metadata <- list(
      pair_signature =
        expected_pair_signature,
      counts = counts,
      starts = numeric(n_files),
      total_assignments = 0
    )

    .isf_atomic_save_rds(
      metadata,
      metadata_path
    )

    return(
      list(
        matrix = NULL,
        counts = counts,
        starts = metadata$starts,
        descriptor_path = descriptor_path,
        total_assignments = 0
      )
    )
  }

  starts <- cumsum(
    c(
      1,
      head(
        counts,
        -1L
      )
    )
  )

  assignment_matrix <-
    bigmemory::filebacked.big.matrix(
      nrow = total_assignments,
      ncol = 3L,
      type = "integer",
      backingfile = backing_name,
      backingpath = cache_dir,
      descriptorfile = basename(
        descriptor_path
      ),
      dimnames = list(
        NULL,
        c(
          "pair_id",
          "precursor",
          "fragment"
        )
      )
    )

  write_pointer <- starts

  message(
    "Stage 2 assignment pass 2/2: writing assignments grouped by mzXML file..."
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = length(pair_batches),
    style = 3
  )

  for (batch_id in seq_along(pair_batches)) {
    rows <- pair_batches[
      [batch_id]
    ]

    precursor_ids <- passed_pairs$precursor[
      rows
    ]

    fragment_ids <- passed_pairs$fragment[
      rows
    ]

    pair_ids <- passed_pairs$stage2_id[
      rows
    ]

    precursor_matrix <- .isf_bm_matrix(
      bm,
      rows = precursor_ids
    )

    fragment_matrix <- .isf_bm_matrix(
      bm,
      rows = fragment_ids
    )

    common <- (
      precursor_matrix > 0 &
        fragment_matrix > 0
    )

    active_files <- which(
      colSums(common) > 0
    )

    for (file_id in active_files) {
      selected_rows <- which(
        common[, file_id]
      )

      number_rows <- length(
        selected_rows
      )

      target_rows <- seq.int(
        from = write_pointer[
          file_id
        ],
        length.out = number_rows
      )

      assignment_matrix[
        target_rows,
      ] <- cbind(
        as.integer(
          pair_ids[
            selected_rows
          ]
        ),
        as.integer(
          precursor_ids[
            selected_rows
          ]
        ),
        as.integer(
          fragment_ids[
            selected_rows
          ]
        )
      )

      write_pointer[file_id] <-
        write_pointer[file_id] +
          number_rows
    }

    rm(
      precursor_matrix,
      fragment_matrix,
      common
    )

    utils::setTxtProgressBar(
      progress,
      batch_id
    )
  }

  close(progress)

  metadata <- list(
    pair_signature =
      expected_pair_signature,
    counts = counts,
    starts = starts,
    total_assignments =
      total_assignments
  )

  .isf_atomic_save_rds(
    metadata,
    metadata_path
  )

  list(
    matrix = assignment_matrix,
    counts = counts,
    starts = starts,
    descriptor_path = descriptor_path,
    total_assignments =
      total_assignments
  )
}


.isf_run_stage2_files <- function(
    file_ids,
    files,
    mz,
    rt,
    mz.tol,
    rt.tol,
    block.width,
    smooth_level,
    assignment_descriptor,
    assignment_counts,
    assignment_starts,
    result_dir,
    workers
) {
  worker <- function(file_id) {
    result_path <- file.path(
      result_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    if (file.exists(result_path)) {
      return(
        list(
          file_id = file_id,
          skipped = TRUE,
          n_correlations = NA_integer_
        )
      )
    }

    count <- assignment_counts[
      file_id
    ]

    if (count <= 0) {
      assignments <- data.table::data.table(
        pair_id = integer(),
        precursor = integer(),
        fragment = integer()
      )
    } else {
      assignment_matrix <-
        bigmemory::attach.big.matrix(
          assignment_descriptor
        )

      rows <- seq.int(
        from = assignment_starts[
          file_id
        ],
        length.out = count
      )

      values <- assignment_matrix[
        rows,
      ]

      values <- matrix(
        as.integer(values),
        nrow = count,
        ncol = 3L
      )

      assignments <- data.table::data.table(
        pair_id = values[, 1L],
        precursor = values[, 2L],
        fragment = values[, 3L]
      )
    }

    .isf_process_file_assignments(
      file_id = file_id,
      assignments = assignments,
      mzxml_file = files[file_id],
      mz = mz,
      rt = rt,
      mz.tol = mz.tol,
      rt.tol = rt.tol,
      block.width = block.width,
      smooth_level = smooth_level,
      result_path = result_path
    )
  }

  old_options <- pbapply::pboptions(
    type = if (interactive()) {
      "timer"
    } else {
      "txt"
    }
  )

  on.exit(
    pbapply::pboptions(
      old_options
    ),
    add = TRUE
  )

  if (workers <= 1L) {
    invisible(
      pbapply::pblapply(
        file_ids,
        worker
      )
    )

    return(invisible(TRUE))
  }

  cluster <- parallel::makePSOCKcluster(
    workers
  )

  on.exit(
    parallel::stopCluster(
      cluster
    ),
    add = TRUE
  )

  parallel::clusterEvalQ(
    cluster,
    {
      library(xcms)
      library(data.table)
      library(bigmemory)
      data.table::setDTthreads(1L)
      NULL
    }
  )

  parallel::clusterExport(
    cluster,
    varlist = c(
      ".isf_peak_smooth",
      ".isf_cor_one_to_many",
      ".isf_atomic_save_rds",
      ".isf_process_file_assignments"
    ),
    envir = environment()
  )

  invisible(
    pbapply::pblapply(
      file_ids,
      worker,
      cl = cluster
    )
  )

  invisible(TRUE)
}


# ------------------------------------------------------------
# Stage-2 final aggregation
# ------------------------------------------------------------
.isf_aggregate_stage2 <- function(
    passed_pairs,
    n_files,
    result_dir,
    peakCOR,
    min_final_valid,
    final_min_proportion
) {
  n_pairs <- nrow(
    passed_pairs
  )

  cor_sum <- numeric(
    n_pairs
  )

  cor_count <- integer(
    n_pairs
  )

  cor_ge_peak <- integer(
    n_pairs
  )

  cor_min <- rep(
    Inf,
    n_pairs
  )

  cor_max <- rep(
    -Inf,
    n_pairs
  )

  progress <- utils::txtProgressBar(
    min = 0,
    max = n_files,
    style = 3
  )

  for (file_id in seq_len(n_files)) {
    result_path <- file.path(
      result_dir,
      sprintf(
        "file_%05d.rds",
        file_id
      )
    )

    if (file.exists(result_path)) {
      current <- readRDS(
        result_path
      )

      if (nrow(current)) {
        valid <- (
          current$pair_id >= 1L &
            current$pair_id <=
              n_pairs &
            is.finite(
              current$cor
            )
        )

        if (any(valid)) {
          ids <- current$pair_id[
            valid
          ]

          values <- current$cor[
            valid
          ]

          cor_sum[ids] <-
            cor_sum[ids] +
              values

          cor_count[ids] <-
            cor_count[ids] +
              1L

          cor_ge_peak[ids] <-
            cor_ge_peak[ids] +
              as.integer(
                values >= peakCOR
              )

          cor_min[ids] <- pmin(
            cor_min[ids],
            values
          )

          cor_max[ids] <- pmax(
            cor_max[ids],
            values
          )
        }
      }

      rm(current)
    }

    utils::setTxtProgressBar(
      progress,
      file_id
    )
  }

  close(progress)

  passed_pairs[
    ,
    final_valid_files :=
      cor_count[stage2_id]
  ]

  passed_pairs[
    ,
    final_mean_cor :=
      data.table::fifelse(
        final_valid_files > 0L,
        cor_sum[stage2_id] /
          final_valid_files,
        NA_real_
      )
  ]

  passed_pairs[
    ,
    final_prop_ge_peak :=
      data.table::fifelse(
        final_valid_files > 0L,
        cor_ge_peak[stage2_id] /
          final_valid_files,
        NA_real_
      )
  ]

  passed_pairs[
    ,
    final_min_cor :=
      data.table::fifelse(
        final_valid_files > 0L,
        cor_min[stage2_id],
        NA_real_
      )
  ]

  passed_pairs[
    ,
    final_max_cor :=
      data.table::fifelse(
        final_valid_files > 0L,
        cor_max[stage2_id],
        NA_real_
      )
  ]

  passed_pairs[
    ,
    final_pass :=
      final_valid_files >=
        min_final_valid &
        is.finite(
          final_mean_cor
        ) &
        final_mean_cor >=
          peakCOR &
        final_prop_ge_peak >=
          final_min_proportion
  ]

  passed_pairs
}


# ------------------------------------------------------------
# Optional list-style output
# ------------------------------------------------------------
.isf_build_groups <- function(
    hits,
    featureTable,
    mz_col,
    rt_col
) {
  if (!nrow(hits)) {
    return(list())
  }

  split_hits <- split(
    hits,
    hits$precursor
  )

  groups <- lapply(
    split_hits,
    function(current_hits) {
      precursor_id <- current_hits$precursor[
        1L
      ]

      fragment_ids <- current_hits$fragment

      table_out <- rbind(
        featureTable[
          precursor_id,
          ,
          drop = FALSE
        ],
        featureTable[
          fragment_ids,
          ,
          drop = FALSE
        ]
      )

      table_out$ppcor <- c(
        0,
        current_hits$final_mean_cor
      )

      table_out$valid_files <- c(
        NA_integer_,
        current_hits$final_valid_files
      )

      table_out$prop_cor_ge_threshold <- c(
        NA_real_,
        current_hits$final_prop_ge_peak
      )

      table_out$ISF_level <- c(
        "Precursor",
        rep(
          "Level_3",
          length(fragment_ids)
        )
      )

      table_out <- table_out[
        order(
          table_out[
            [mz_col]
          ],
          decreasing = TRUE
        ),
        ,
        drop = FALSE
      ]

      rownames(table_out) <- NULL

      table_out
    }
  )

  precursor_ids <- as.integer(
    names(split_hits)
  )

  feature_names <- rownames(
    featureTable
  )

  if (
    is.null(feature_names) ||
    any(!nzchar(feature_names))
  ) {
    feature_names <- as.character(
      seq_len(
        nrow(featureTable)
      )
    )
  }

  names(groups) <- paste0(
    feature_names[
      precursor_ids
    ],
    "_",
    round(
      featureTable[
        precursor_ids,
        mz_col
      ],
      2
    ),
    "_",
    round(
      featureTable[
        precursor_ids,
        rt_col
      ],
      0
    )
  )

  groups
}


# ------------------------------------------------------------
# Main function
# ------------------------------------------------------------
ISFlevel3_two_stage <- function(
    MS1directory,
    MS1.files,
    featureTable,

    mz_col = "mz",
    rt_col = "rt",
    intensity_cols = NULL,

    # Final scientific thresholds
    peakCOR = 0.80,
    loss = 10,
    mz.tol = 0.01,
    rt.tol = 30,
    candidate.rt = 10,

    # Candidate presence requirements
    min_copresent_files = 3L,

    # Stage 1: representative-file screen
    stage1_files_per_pair = 5L,
    screenCOR = 0.65,
    stage1_min_valid = 2L,
    stage1_fail_open_sparse = TRUE,

    # Optional cross-sample intensity prefilter.
    # NULL means disabled and gives higher recall.
    prefilter_cor = NULL,
    prefilter_samples = 64L,

    # Stage 2: final all-co-present-file decision
    min_final_valid = 3L,

    # 0 means final decision is based on mean EIC correlation only.
    # Example: 0.60 additionally requires at least 60% of valid files
    # to have correlation >= peakCOR.
    final_min_proportion = 0,

    # Chunk and memory controls
    candidate_feature_chunk = 500L,
    candidate_batch_size = 250L,
    stage2_pair_batch_size = 1000L,
    block.width = 60,
    smooth_level = 2L,

    # File-level parallelism. Start with 1-2 on Windows.
    workers = 2L,

    # Cache and resume
    work_dir = file.path(
      MS1directory,
      "ISFlevel3_two_stage_work"
    ),
    run_id = "default",
    rebuild_intensity_cache = FALSE,
    rebuild_candidates = FALSE,
    rebuild_stage1 = FALSE,
    rebuild_stage2_assignments = FALSE,
    rebuild_stage2 = FALSE,

    # Output
    build_groups = FALSE
) {
  .isf_check_packages()

  if (
    !all(
      c(
        mz_col,
        rt_col
      ) %in%
        names(featureTable)
    )
  ) {
    stop(
      "featureTable must contain columns: ",
      mz_col,
      " and ",
      rt_col
    )
  }

  files <- as.character(
    MS1.files
  )

  missing_files <- !file.exists(
    files
  )

  if (any(missing_files)) {
    files[
      missing_files
    ] <- file.path(
      MS1directory,
      files[
        missing_files
      ]
    )
  }

  if (any(!file.exists(files))) {
    stop(
      "The following mzXML files were not found:\n",
      paste(
        files[
          !file.exists(files)
        ],
        collapse = "\n"
      )
    )
  }

  files <- normalizePath(
    files,
    winslash = "/",
    mustWork = TRUE
  )

  n_files <- length(
    files
  )

  n_features <- nrow(
    featureTable
  )

  if (is.null(intensity_cols)) {
    if (
      ncol(featureTable) <
        4L + n_files
    ) {
      stop(
        "intensity_cols was not supplied, and featureTable ",
        "does not have enough columns for 5:(4 + n_files)."
      )
    }

    intensity_cols <- 5:(
      4 + n_files
    )
  }

  if (is.character(intensity_cols)) {
    intensity_cols <- match(
      intensity_cols,
      names(featureTable)
    )
  }

  if (
    anyNA(intensity_cols) ||
    length(intensity_cols) !=
      n_files
  ) {
    stop(
      "intensity_cols must contain exactly one intensity ",
      "column for every mzXML file, in the same order as MS1.files."
    )
  }

  mz <- suppressWarnings(
    as.numeric(
      featureTable[
        [mz_col]
      ]
    )
  )

  rt <- suppressWarnings(
    as.numeric(
      featureTable[
        [rt_col]
      ]
    )
  )

  if (
    any(!is.finite(mz)) ||
    any(!is.finite(rt))
  ) {
    stop(
      "The mz and rt columns must contain finite numeric values."
    )
  }

  if (
    min_copresent_files < 1L ||
    stage1_files_per_pair < 1L ||
    stage1_min_valid < 1L ||
    min_final_valid < 1L
  ) {
    stop(
      "Presence and valid-file parameters must be at least 1."
    )
  }

  if (
    final_min_proportion < 0 ||
    final_min_proportion > 1
  ) {
    stop(
      "final_min_proportion must be between 0 and 1."
    )
  }

  physical_cores <- parallel::detectCores(
    logical = FALSE
  )

  if (
    is.na(physical_cores) ||
    physical_cores < 1L
  ) {
    physical_cores <- 1L
  }

  workers <- max(
    1L,
    min(
      as.integer(workers),
      physical_cores,
      n_files
    )
  )

  candidate_feature_chunk <- max(
    50L,
    as.integer(
      candidate_feature_chunk
    )
  )

  candidate_batch_size <- max(
    20L,
    as.integer(
      candidate_batch_size
    )
  )

  stage2_pair_batch_size <- max(
    50L,
    as.integer(
      stage2_pair_batch_size
    )
  )

  prefilter_samples <- min(
    n_files,
    max(
      2L,
      as.integer(
        prefilter_samples
      )
    )
  )

  data.table::setDTthreads(
    if (workers > 1L) {
      1L
    } else {
      2L
    }
  )

  work_dir <- .isf_make_dir(
    work_dir
  )

  data_signature <- .isf_data_signature(
    files = files,
    featureTable = featureTable,
    mz = mz,
    rt = rt,
    intensity_cols = intensity_cols
  )

  run_signature <- .isf_run_signature(
    data_signature = data_signature,
    run_id = run_id,
    peakCOR = peakCOR,
    screenCOR = screenCOR,
    loss = loss,
    mz.tol = mz.tol,
    rt.tol = rt.tol,
    candidate.rt = candidate.rt,
    min_copresent_files =
      min_copresent_files,
    stage1_files_per_pair =
      stage1_files_per_pair,
    stage1_min_valid =
      stage1_min_valid,
    stage1_fail_open_sparse =
      stage1_fail_open_sparse,
    prefilter_cor =
      prefilter_cor,
    prefilter_samples =
      prefilter_samples,
    min_final_valid =
      min_final_valid,
    final_min_proportion =
      final_min_proportion,
    candidate_feature_chunk =
      candidate_feature_chunk,
    candidate_batch_size =
      candidate_batch_size,
    stage2_pair_batch_size =
      stage2_pair_batch_size,
    block.width = block.width,
    smooth_level = smooth_level
  )

  intensity_cache_dir <- .isf_make_dir(
    file.path(
      work_dir,
      paste0(
        "intensity_",
        data_signature
      )
    )
  )

  run_dir <- .isf_make_dir(
    file.path(
      work_dir,
      paste0(
        "run_",
        run_signature
      )
    )
  )

  candidate_chunk_dir <- .isf_make_dir(
    file.path(
      run_dir,
      "candidate_chunks"
    )
  )

  stage1_assignment_dir <- .isf_make_dir(
    file.path(
      run_dir,
      "stage1_assignments"
    )
  )

  stage1_result_dir <- .isf_make_dir(
    file.path(
      run_dir,
      "stage1_results"
    )
  )

  stage2_assignment_dir <- .isf_make_dir(
    file.path(
      run_dir,
      "stage2_assignments"
    )
  )

  stage2_result_dir <- .isf_make_dir(
    file.path(
      run_dir,
      "stage2_results"
    )
  )

  combined_pairs_path <- file.path(
    run_dir,
    "candidate_pairs.rds"
  )

  combined_assignments_path <- file.path(
    run_dir,
    "stage1_representative_assignments.rds"
  )

  stage1_assignment_complete <- file.path(
    stage1_assignment_dir,
    "_COMPLETE"
  )

  stage1_screened_path <- file.path(
    run_dir,
    "stage1_screened_pairs.rds"
  )

  stage2_all_pairs_path <- file.path(
    run_dir,
    "stage2_all_pairs.rds"
  )

  hits_rds_path <- file.path(
    run_dir,
    "ISF_Level3_hits.rds"
  )

  hits_csv_path <- file.path(
    run_dir,
    "ISF_Level3_hits.csv"
  )

  if (rebuild_candidates) {
    .isf_clear_dir(
      candidate_chunk_dir
    )

    .isf_clear_dir(
      stage1_assignment_dir
    )

    .isf_clear_dir(
      stage1_result_dir
    )

    .isf_clear_dir(
      stage2_assignment_dir
    )

    .isf_clear_dir(
      stage2_result_dir
    )

    unlink(
      c(
        combined_pairs_path,
        combined_assignments_path,
        stage1_screened_path,
        stage2_all_pairs_path,
        hits_rds_path,
        hits_csv_path
      ),
      force = TRUE
    )
  } else if (rebuild_stage1) {
    .isf_clear_dir(
      stage1_result_dir
    )

    .isf_clear_dir(
      stage2_assignment_dir
    )

    .isf_clear_dir(
      stage2_result_dir
    )

    unlink(
      c(
        stage1_screened_path,
        stage2_all_pairs_path,
        hits_rds_path,
        hits_csv_path
      ),
      force = TRUE
    )
  } else if (rebuild_stage2_assignments) {
    .isf_clear_dir(
      stage2_assignment_dir
    )

    .isf_clear_dir(
      stage2_result_dir
    )

    unlink(
      c(
        stage2_all_pairs_path,
        hits_rds_path,
        hits_csv_path
      ),
      force = TRUE
    )
  } else if (rebuild_stage2) {
    .isf_clear_dir(
      stage2_result_dir
    )

    unlink(
      c(
        stage2_all_pairs_path,
        hits_rds_path,
        hits_csv_path
      ),
      force = TRUE
    )
  }

  message("")
  message(
    "============================================================"
  )
  message(
    "ISF Level 3 two-stage analysis"
  )
  message(
    "Features: ",
    format(
      n_features,
      big.mark = ","
    ),
    "; mzXML files: ",
    format(
      n_files,
      big.mark = ","
    ),
    "; workers: ",
    workers
  )
  message(
    "Run directory: ",
    run_dir
  )
  message(
    "============================================================"
  )

  message("")
  message(
    "Stage 1/7 - Preparing intensity matrix"
  )

  bm <- .isf_prepare_intensity_matrix(
    featureTable = featureTable,
    intensity_cols = intensity_cols,
    cache_dir = intensity_cache_dir,
    rebuild =
      rebuild_intensity_cache
  )

  prefilter_file_ids <- unique(
    as.integer(
      round(
        seq(
          from = 1,
          to = n_files,
          length.out =
            prefilter_samples
        )
      )
    )
  )

  message("")
  message(
    "Stage 2/7 - Generating RT/mass candidates"
  )

  candidate_result <- .isf_prepare_candidates(
    bm = bm,
    mz = mz,
    rt = rt,
    candidate.rt = candidate.rt,
    loss = loss,
    min_copresent_files =
      min_copresent_files,
    stage1_files_per_pair =
      stage1_files_per_pair,
    candidate_feature_chunk =
      candidate_feature_chunk,
    candidate_batch_size =
      candidate_batch_size,
    prefilter_cor =
      prefilter_cor,
    prefilter_file_ids =
      prefilter_file_ids,
    candidate_chunk_dir =
      candidate_chunk_dir,
    combined_pairs_path =
      combined_pairs_path,
    combined_assignments_path =
      combined_assignments_path,
    rebuild = rebuild_candidates
  )

  pairs <- candidate_result$pairs

  stage1_assignments <-
    candidate_result$assignments

  rm(candidate_result)

  if (!nrow(pairs)) {
    warning(
      "No candidate pairs passed the RT, mass-loss, and ",
      "co-presence requirements."
    )

    empty_hits <- .isf_empty_hits()

    .isf_atomic_save_rds(
      empty_hits,
      hits_rds_path
    )

    data.table::fwrite(
      empty_hits,
      hits_csv_path
    )

    return(
      list(
        hits = empty_hits,
        groups = NULL,
        run_dir = run_dir,
        candidate_pairs_file =
          combined_pairs_path,
        stage1_pairs_file =
          stage1_screened_path,
        stage2_pairs_file =
          stage2_all_pairs_path,
        parameters = list(
          peakCOR = peakCOR,
          screenCOR = screenCOR,
          min_copresent_files =
            min_copresent_files,
          min_final_valid =
            min_final_valid,
          run_signature =
            run_signature
        )
      )
    )
  }

  message(
    "Candidate pairs: ",
    format(
      nrow(pairs),
      big.mark = ","
    )
  )

  message("")
  message(
    "Stage 3/7 - Stage-1 representative-file EIC screen"
  )

  .isf_write_assignments_by_file(
    assignments =
      stage1_assignments,
    n_files = n_files,
    assignment_dir =
      stage1_assignment_dir,
    complete_flag =
      stage1_assignment_complete,
    rebuild =
      rebuild_candidates
  )

  rm(stage1_assignments)
  gc(FALSE)

  stage1_missing_files <- which(
    !file.exists(
      file.path(
        stage1_result_dir,
        sprintf(
          "file_%05d.rds",
          seq_len(n_files)
        )
      )
    )
  )

  if (length(stage1_missing_files)) {
    .isf_run_stage1_files(
      file_ids =
        stage1_missing_files,
      files = files,
      mz = mz,
      rt = rt,
      mz.tol = mz.tol,
      rt.tol = rt.tol,
      block.width =
        block.width,
      smooth_level =
        smooth_level,
      assignment_dir =
        stage1_assignment_dir,
      result_dir =
        stage1_result_dir,
      workers = workers
    )
  } else {
    message(
      "All Stage-1 file checkpoints already exist."
    )
  }

  message("")
  message(
    "Stage 4/7 - Aggregating Stage-1 results"
  )

  if (
    !file.exists(stage1_screened_path) ||
      rebuild_stage1 ||
      rebuild_candidates
  ) {
    pairs <- .isf_aggregate_stage1(
      pairs = pairs,
      n_files = n_files,
      stage1_result_dir =
        stage1_result_dir,
      peakCOR = peakCOR,
      screenCOR = screenCOR,
      stage1_min_valid =
        stage1_min_valid,
      stage1_fail_open_sparse =
        stage1_fail_open_sparse
    )

    .isf_atomic_save_rds(
      pairs,
      stage1_screened_path
    )
  } else {
    pairs <- readRDS(
      stage1_screened_path
    )
  }

  passed_pairs <- pairs[
    stage1_pass == TRUE
  ]

  message(
    "Pairs passing Stage 1: ",
    format(
      nrow(passed_pairs),
      big.mark = ","
    ),
    " / ",
    format(
      nrow(pairs),
      big.mark = ","
    )
  )

  rm(pairs)
  gc(FALSE)

  if (!nrow(passed_pairs)) {
    warning(
      "No candidate pairs passed the Stage-1 EIC screen."
    )

    empty_hits <- .isf_empty_hits()

    .isf_atomic_save_rds(
      empty_hits,
      hits_rds_path
    )

    data.table::fwrite(
      empty_hits,
      hits_csv_path
    )

    return(
      list(
        hits = empty_hits,
        groups = NULL,
        run_dir = run_dir,
        candidate_pairs_file =
          combined_pairs_path,
        stage1_pairs_file =
          stage1_screened_path,
        stage2_pairs_file =
          stage2_all_pairs_path,
        parameters = list(
          peakCOR = peakCOR,
          screenCOR = screenCOR,
          min_copresent_files =
            min_copresent_files,
          min_final_valid =
            min_final_valid,
          run_signature =
            run_signature
        )
      )
    )
  }

  passed_pairs[
    ,
    stage2_id := seq_len(
      .N
    )
  ]

  message("")
  message(
    "Stage 5/7 - Building all-co-present-file assignments"
  )

  stage2_assignment <- .isf_prepare_stage2_assignments(
    passed_pairs =
      passed_pairs,
    bm = bm,
    n_files = n_files,
    cache_dir =
      stage2_assignment_dir,
    pair_batch_size =
      stage2_pair_batch_size,
    rebuild =
      rebuild_stage2_assignments ||
        rebuild_stage1 ||
        rebuild_candidates
  )

  message(
    "Stage-2 pair-file assignments: ",
    format(
      stage2_assignment$total_assignments,
      big.mark = ","
    )
  )

  message("")
  message(
    "Stage 6/7 - Stage-2 EIC calculation in every co-present file"
  )

  stage2_missing_files <- which(
    !file.exists(
      file.path(
        stage2_result_dir,
        sprintf(
          "file_%05d.rds",
          seq_len(n_files)
        )
      )
    )
  )

  if (length(stage2_missing_files)) {
    .isf_run_stage2_files(
      file_ids =
        stage2_missing_files,
      files = files,
      mz = mz,
      rt = rt,
      mz.tol = mz.tol,
      rt.tol = rt.tol,
      block.width =
        block.width,
      smooth_level =
        smooth_level,
      assignment_descriptor =
        stage2_assignment$descriptor_path,
      assignment_counts =
        stage2_assignment$counts,
      assignment_starts =
        stage2_assignment$starts,
      result_dir =
        stage2_result_dir,
      workers = workers
    )
  } else {
    message(
      "All Stage-2 file checkpoints already exist."
    )
  }

  rm(stage2_assignment)
  gc(FALSE)

  message("")
  message(
    "Stage 7/7 - Final aggregation and peakCOR decision"
  )

  if (
    !file.exists(stage2_all_pairs_path) ||
      rebuild_stage2 ||
      rebuild_stage2_assignments ||
      rebuild_stage1 ||
      rebuild_candidates
  ) {
    passed_pairs <- .isf_aggregate_stage2(
      passed_pairs =
        passed_pairs,
      n_files = n_files,
      result_dir =
        stage2_result_dir,
      peakCOR = peakCOR,
      min_final_valid =
        min_final_valid,
      final_min_proportion =
        final_min_proportion
    )

    .isf_atomic_save_rds(
      passed_pairs,
      stage2_all_pairs_path
    )
  } else {
    passed_pairs <- readRDS(
      stage2_all_pairs_path
    )
  }

  hits <- passed_pairs[
    final_pass == TRUE
  ]

  data.table::setorder(
    hits,
    precursor,
    -final_mean_cor,
    fragment
  )

  .isf_atomic_save_rds(
    hits,
    hits_rds_path
  )

  data.table::fwrite(
    hits,
    hits_csv_path
  )

  groups <- NULL

  if (build_groups) {
    groups <- .isf_build_groups(
      hits = hits,
      featureTable =
        featureTable,
      mz_col = mz_col,
      rt_col = rt_col
    )
  }

  message("")
  message(
    "============================================================"
  )
  message(
    "Finished."
  )
  message(
    "Final Level-3 ISF pairs: ",
    format(
      nrow(hits),
      big.mark = ","
    )
  )
  message(
    "Results CSV: ",
    hits_csv_path
  )
  message(
    "Checkpoint directory: ",
    run_dir
  )
  message(
    "============================================================"
  )

  list(
    hits = hits,
    groups = groups,
    run_dir = run_dir,
    hits_csv = hits_csv_path,
    hits_rds = hits_rds_path,
    candidate_pairs_file =
      combined_pairs_path,
    stage1_pairs_file =
      stage1_screened_path,
    stage2_pairs_file =
      stage2_all_pairs_path,
    parameters = list(
      peakCOR = peakCOR,
      screenCOR = screenCOR,
      loss = loss,
      mz.tol = mz.tol,
      rt.tol = rt.tol,
      candidate.rt =
        candidate.rt,
      min_copresent_files =
        min_copresent_files,
      stage1_files_per_pair =
        stage1_files_per_pair,
      stage1_min_valid =
        stage1_min_valid,
      stage1_fail_open_sparse =
        stage1_fail_open_sparse,
      prefilter_cor =
        prefilter_cor,
      prefilter_samples =
        prefilter_samples,
      min_final_valid =
        min_final_valid,
      final_min_proportion =
        final_min_proportion,
      candidate_feature_chunk =
        candidate_feature_chunk,
      candidate_batch_size =
        candidate_batch_size,
      stage2_pair_batch_size =
        stage2_pair_batch_size,
      block.width =
        block.width,
      smooth_level =
        smooth_level,
      workers = workers,
      data_signature =
        data_signature,
      run_signature =
        run_signature
    )
  )
}


# ============================================================
# Recommended example for 100,000+ features and 1000 mzXML files
# ============================================================
#
# source("ISFlevel3_two_stage.R")
#
# MS1directory <- "D:/MS1_mzXML"
#
# MS1.files <- list.files(
#   MS1directory,
#   pattern = "\\.mzXML$",
#   full.names = TRUE
# )
#
# IMPORTANT:
# The sample intensity columns must correspond one-to-one and in
# exactly the same order as MS1.files.
#
# result <- ISFlevel3_two_stage(
#   MS1directory = MS1directory,
#   MS1.files = MS1.files,
#   featureTable = featureTable,
#   intensity_cols = 5:(4 + length(MS1.files)),
#
#   peakCOR = 0.80,
#   loss = 10,
#   mz.tol = 0.01,
#   rt.tol = 30,
#   candidate.rt = 10,
#
#   # At least three files where both features are present.
#   # Change all three "3" values to 1 if single-file candidates
#   # must also be retained.
#   min_copresent_files = 3,
#
#   # Stage 1: use up to five strong representative files.
#   stage1_files_per_pair = 5,
#   screenCOR = 0.65,
#   stage1_min_valid = 2,
#   stage1_fail_open_sparse = TRUE,
#
#   # Disabled by default to avoid losing a true EIC-correlated
#   # fragment merely because its cross-sample abundance correlation
#   # is low.
#   prefilter_cor = NULL,
#   prefilter_samples = 64,
#
#   # Stage 2 final decision: mean EIC correlation >= 0.80
#   # in all valid co-present files.
#   min_final_valid = 3,
#   final_min_proportion = 0,
#
#   candidate_feature_chunk = 500,
#   candidate_batch_size = 250,
#   stage2_pair_batch_size = 1000,
#   block.width = 60,
#   smooth_level = 2,
#
#   # Start with 1 or 2 on Windows.
#   workers = 2,
#
#   work_dir = "D:/ISFlevel3_two_stage_work",
#   run_id = "dataset_001",
#
#   rebuild_intensity_cache = FALSE,
#   rebuild_candidates = FALSE,
#   rebuild_stage1 = FALSE,
#   rebuild_stage2_assignments = FALSE,
#   rebuild_stage2 = FALSE,
#
#   build_groups = FALSE
# )
#
# result$hits
# result$hits_csv
#
# Optional stricter consistency rule:
#   final_min_proportion = 0.60
# means:
#   - final mean EIC correlation must be >= peakCOR, and
#   - at least 60% of valid files must individually have
#     correlation >= peakCOR.
#
# To include candidates present in only one file:
#   min_copresent_files = 1
#   stage1_min_valid = 1
#   min_final_valid = 1
#
# To disable Stage-1 correlation filtering completely while still
# using representative files for diagnostics:
#   screenCOR = NULL
#
# ============================================================
